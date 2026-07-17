import sys

with open('start-docker.ps1', 'r', encoding='utf-8-sig') as f:
    content = f.read()

content = content.replace(' *>', ' 2>&1 | Out-Null')

with open('start-docker.ps1', 'w', encoding='utf-8-sig') as f:
    f.write(content)
