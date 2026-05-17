import urllib.request, json, sys

def post_json(url, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def get_json(url):
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode())

if __name__ == '__main__':
    base = 'http://127.0.0.1:5173'
    try:
        campaign = {
            'sender_id': 'local-dryrun',
            'name': 'Demo campaign',
            'campaign_type': 'newsletter',
            'subject': 'Demo: OmniAI dry run',
            'purpose': 'Demo sending flow',
            'html_body': '<h1>Hello {{first_name}}</h1><p>This is a dry run.</p>',
            'plain_body': 'Hello {{first_name}}\nThis is a dry run.',
            'delay_seconds': 0.1,
        }
        print('Creating campaign...')
        create = post_json(base + '/api/campaigns', campaign)
        print('Create response:', json.dumps(create, indent=2))

        state = get_json(base + '/api/state')
        campaigns = state.get('campaigns', [])
        cid = campaigns[-1]['id'] if campaigns else None
        if not cid:
            print('No campaign id found', file=sys.stderr); sys.exit(1)
        print('Campaign id:', cid)

        print('Sending test email to aisha@example.com...')
        res = post_json(base + f'/api/campaigns/{cid}/send-test', {'test_email':'aisha@example.com'})
        print('Send-test result:', json.dumps(res, indent=2))
    except Exception as e:
        print('Error:', e, file=sys.stderr)
        raise
