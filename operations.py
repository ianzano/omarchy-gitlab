#!/usr/bin/env python3
"""On-demand job reads and explicit GitLab actions, using the shared request budget."""
import argparse
import json
import re
import time

from backend import Client, ClientError, state_session, text, write_state

JOBS = '''query Jobs($path: ID!, $iid: ID!, $after: String) {
  project(fullPath: $path) { pipeline(iid: $iid) {
    id ref source retryable userPermissions { updatePipeline }
    mergeRequest { iid }
    jobs(first: 50, after: $after, retried: false) {
      nodes { id name status retryable playable canPlayJob stage { name } }
      pageInfo { hasNextPage endCursor }
    }
  } }
}'''
INFO = '''query ActionInfo($path: ID!, $iid: ID!) {
  project(fullPath: $path) { pipeline(iid: $iid) {
    id ref source retryable userPermissions { updatePipeline } mergeRequest { iid }
  } }
}'''
MUTATIONS = {
    'retry-pipeline': ('pipelineRetry', '''mutation RetryPipeline($id: CiPipelineID!) {
      pipelineRetry(input: {id: $id}) { errors pipeline { id status } }
    }'''),
    'new-pipeline': ('pipelineCreate', '''mutation NewPipeline($project: ID!, $ref: String!, $mr: String) {
      pipelineCreate(input: {projectPath: $project, ref: $ref, mergeRequestIid: $mr}) { errors pipeline { id status } }
    }'''),
    'retry-job': ('jobRetry', '''mutation RetryJob($id: CiProcessableID!) {
      jobRetry(input: {id: $id}) { errors job { id status } }
    }'''),
    'play-job': ('jobPlay', '''mutation PlayJob($id: CiProcessableID!) {
      jobPlay(input: {id: $id}) { errors job { id status } }
    }''')
}


def fetch_jobs(client, row, force=False):
    saved = client.state.setdefault('jobCache', {}).get(row['gid'])
    if saved and not force and time.time() - saved['fetchedAt'] < 20:
        return saved
    jobs, cursor = [], None
    for _ in range(10):
        project = client.query(JOBS, {'path': row['project'], 'iid': row['iid'], 'after': cursor}).get('project')
        pipeline = (project or {}).get('pipeline')
        if not pipeline or pipeline['id'] != row['gid']:
            raise ClientError('access', 'This pipeline is no longer accessible.')
        connection = pipeline['jobs']
        for job in connection['nodes']:
            if job and re.fullmatch(r'gid://gitlab/Ci::(?:Build|Bridge)/\d+', job.get('id', '')):
                jobs.append(dict(id=job['id'], name=text(job.get('name'), 200), status=text(job.get('status'), 32).lower(),
                                 stage=text((job.get('stage') or {}).get('name'), 100),
                                 retryable=bool(job.get('retryable')), playable=bool(job.get('playable')) and bool(job.get('canPlayJob'))))
        page = connection['pageInfo']
        if not page['hasNextPage']:
            break
        after = page.get('endCursor')
        if not after or after == cursor:
            raise ClientError('response', 'GitLab job pagination did not advance.')
        cursor = after
    result = dict(jobs=jobs, more=bool(page['hasNextPage']), fetchedAt=time.time())
    client.state['jobCache'][row['gid']] = result
    client.state['jobCache'] = dict(sorted(client.state['jobCache'].items(), key=lambda item: item[1]['fetchedAt'], reverse=True)[:20])
    return result


def execute(client, state_path, row, operation, job_id=''):
    if operation not in MUTATIONS:
        raise ClientError('config', 'Unknown pipeline action.')
    key = operation + ':' + row['gid'] + ':' + job_id
    previous = client.state.setdefault('actions', {}).get(key)
    if previous and time.time() < previous['until']:
        return previous['result']
    variables = {'id': row['gid']}
    if operation.endswith('-job'):
        jobs = fetch_jobs(client, row, force=True)['jobs']
        job = next((job for job in jobs if job['id'] == job_id), None)
        if not job or not job['playable' if operation == 'play-job' else 'retryable']:
            raise ClientError('action', 'GitLab does not allow that action for this job in its current state.')
        variables = {'id': job_id}
    else:
        project = client.query(INFO, {'path': row['project'], 'iid': row['iid']}).get('project')
        pipeline = (project or {}).get('pipeline')
        if not pipeline or pipeline['id'] != row['gid'] or not pipeline['userPermissions']['updatePipeline']:
            raise ClientError('permission', 'Your GitLab account cannot run this pipeline.')
        if operation == 'retry-pipeline' and not pipeline['retryable']:
            raise ClientError('action', 'There are no failed or canceled jobs to retry. Use Run again for a new pipeline.')
        if operation == 'new-pipeline':
            mr = (pipeline.get('mergeRequest') or {}).get('iid')
            variables = {'project': row['project'], 'ref': pipeline['ref'], 'mr': str(mr) if mr else None}
    # Persist uncertainty before the write. A crash/timeout must never cause an automatic repeat.
    uncertain = dict(ok=False, code='uncertain', message='GitLab did not confirm the action. Check the pipeline before trying again.')
    client.state['actions'] = {k: value for k, value in client.state['actions'].items() if value['until'] > time.time()}
    client.state['actions'][key] = dict(until=time.time() + 120, result=uncertain)
    write_state(state_path, client.state)
    field, mutation = MUTATIONS[operation]
    try:
        payload = client.query(mutation, variables).get(field)
    except ClientError as error:
        if error.code in ('rate', 'budget', 'permission', 'auth'):
            client.state['actions'].pop(key, None)
            raise
        client.state['nextAttemptAt'] = 0
        return uncertain
    if not payload:
        return uncertain
    if payload.get('errors'):
        client.state['actions'].pop(key, None)
        raise ClientError('action', text(' · '.join(str(value) for value in payload['errors']), 700))
    item = payload.get('job') if operation.endswith('-job') else payload.get('pipeline')
    if not item:
        return uncertain
    result = dict(ok=True, message='Action accepted by GitLab.', itemId=text(item.get('id')), status=text(item.get('status'), 32).lower())
    client.state['actions'][key] = dict(until=time.time() + 10, result=result)
    client.state.setdefault('stageCache', {}).pop(row['gid'], None)
    client.state.get('jobCache', {}).pop(row['gid'], None)
    client.state['nextAttemptAt'] = 0
    return result


def run(host, ident, operation, job_id='', *, token=None, client_factory=None, cache_root=None):
    with state_session(host, token, cache_root) as (state, token, directory, path):
        row = next((row for row in state.get('snapshot', {}).get('pipelines', []) if row['id'] == ident), None)
        if not row:
            raise ClientError('access', 'Refresh the pipeline list before choosing an action.')
        client = client_factory(host, state) if client_factory else Client(host, token=token, state=state)
        try:
            if operation == 'jobs':
                return dict(ok=True, **fetch_jobs(client, row))
            return execute(client, path, row, operation, job_id)
        except ClientError as error:
            return dict(ok=False, code=error.code, message=str(error), retryAt=error.retry_at * 1000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['jobs', *MUTATIONS])
    parser.add_argument('--host', default='gitlab.com')
    parser.add_argument('--pipeline', required=True)
    parser.add_argument('--job', default='')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.host, args.pipeline, args.operation, args.job)))
    except (ClientError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(ok=False, code=getattr(error, 'code', 'response'), message=str(error) if isinstance(error, ClientError) else 'Could not complete the GitLab action.')))


if __name__ == '__main__':
    main()
