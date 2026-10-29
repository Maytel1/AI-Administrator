import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

# Count occurrences of {csrf_field(csrf)}
count = content.count("{csrf_field(csrf)}")
print(f"Found {count} occurrences of {{csrf_field(csrf)}}")

# Replace non-f-string versions: '{csrf_field(csrf)}' -> f"{csrf_field(csrf)}"
# We need to replace the pattern: '...{csrf_field(csrf)}' where it's NOT already f"{...}"
import re
# Match: a line containing '{csrf_field(csrf)}' that starts with '                "' (8 spaces + quote, not f-quote)
old_pattern = "                \"{csrf_field(csrf)}\""
new_pattern = '                f"{csrf_field(csrf)}"'

new_content = content.replace(old_pattern, new_pattern)
count2 = new_content.count('f"{csrf_field(csrf)}"')
print(f"After replacement: {count2} f-string occurrences")

with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Done")
