import re

with open('/media/user/New Volume/POLICE_DRONE/dvas_analytics/anpr/main.py', 'r') as f:
    content = f.read()

# The by-mission block starts at `# ── Mission vehicle history` and goes up to `if __name__ == "__main__":`
by_mission_pattern = r'(# ── Mission vehicle history ──.*?)(?=\nif __name__ == "__main__":)'
by_mission_match = re.search(by_mission_pattern, content, re.DOTALL)
if by_mission_match:
    by_mission_code = by_mission_match.group(1)
    # Remove it from the original place
    content = content.replace(by_mission_code, '')
    
    # Find where to insert it: right before @app.get("/plates/{plate_text}")
    insert_pattern = r'(@app\.get\("/plates/\{plate_text\}"\))'
    content = re.sub(insert_pattern, by_mission_code + r'\n\n\1', content, count=1)
    
    with open('/media/user/New Volume/POLICE_DRONE/dvas_analytics/anpr/main.py', 'w') as f:
        f.write(content)
    print("Fixed route order")
else:
    print("Could not find by-mission block")
