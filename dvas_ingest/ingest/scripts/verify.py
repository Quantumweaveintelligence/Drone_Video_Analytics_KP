import sys

def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()

main    = read('ingest/api/main.py')
models  = read('ingest/api/models.py')
compose = read('ingest/docker-compose.yml')
config  = read('ingest/api/config.py')
qm      = read('ingest/api/queue_manager.py')

dqs_block = models.split('class DroneQueueStats')[1].split('class ')[0]

tests = [
    ('rabbitmq_connected NOT in main.py',  'rabbitmq_connected' not in main),
    ('redis_connected     IN main.py',      'redis_connected' in main),
    ('ReadinessResponse redis= in main',    'redis    =' in main),
    ('redis_connected     IN models.py',    'redis_connected' in models),
    ('DroneQueueStats has no drone_id',     'drone_id:' not in dqs_block),
    ('DroneQueueStats has no messages_unacked', 'messages_unacked' not in dqs_block),
    ('redis image         IN compose',      'image: redis' in compose),
    ('REDIS_URL           IN compose',      'REDIS_URL' in compose),
    ('rabbitmq service NOT IN compose',     'rabbitmq:' not in compose),
    ('REDIS_URL           IN config.py',    'REDIS_URL' in config),
    ('ALLOWED_ROLES       IN config.py',    'ALLOWED_ROLES' in config),
    ('last_frame_at ISO   IN main.py',      'strftime' in main),
    ('datetime imported   IN main.py',      'import datetime' in main),
    ('redis.asyncio       IN queue_manager','redis.asyncio' in qm),
]

print()
all_pass = True
for name, result in tests:
    mark = 'PASS' if result else 'FAIL'
    if not result:
        all_pass = False
    print(f'  [{mark}] {name}')

print()
print('ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED')
sys.exit(0 if all_pass else 1)
