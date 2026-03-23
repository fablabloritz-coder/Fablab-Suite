"""
Quick validation: Jinja2 templates compile without syntax errors
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
os.environ["DB_PATH"] = temp_db

from app import app
from jinja2 import TemplateError

print("🔍 Validating Jinja2 templates...\n")

# Test master.html compilation
try:
    with app.app_context():
        template = app.jinja_env.get_template('master.html')
        print("✅ master.html compiles correctly")
except TemplateError as e:
    print(f"❌ master.html error: {e}")
    sys.exit(1)

# Test search.html compilation
try:
    with app.app_context():
        template = app.jinja_env.get_template('search.html')
        print("✅ search.html compiles correctly")
except TemplateError as e:
    print(f"❌ search.html error: {e}")
    sys.exit(1)

print("\n✅ All templates validated successfully!")

# Cleanup
if os.path.exists(temp_db):
    try:
        os.remove(temp_db)
    except:
        pass
