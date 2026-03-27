#!/usr/bin/env bash
# dispatch.sh — copy generated files into the project and run migrations
# Run from the project root: bash dispatch.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FILES_DIR="$SCRIPT_DIR/files"

echo "==> Copying files..."

# emails/
cp "$FILES_DIR/emails_models.py"           emails/models.py
cp "$FILES_DIR/tasks.py"                   emails/tasks.py
mkdir -p emails/migrations
# Only write the migration if it doesn't already exist
if [ ! -f emails/migrations/0001_initial.py ]; then
  cp "$FILES_DIR/emails_migration_0001.py" emails/migrations/0001_initial.py
  echo "    Created emails/migrations/0001_initial.py"
else
  echo "    Skipped emails/migrations/0001_initial.py (already exists)"
fi
# Ensure emails has an __init__.py for migrations
touch emails/migrations/__init__.py

# llm/
cp "$FILES_DIR/pipeline.py"                llm/pipeline.py
cp "$FILES_DIR/extractor.py"               llm/extractor.py

# dashboard/
cp "$FILES_DIR/views.py"                   dashboard/views.py

# templates/
cp "$FILES_DIR/base.html"                  project/templates/base.html
cp "$FILES_DIR/queue.html"                 project/templates/dashboard/queue.html

echo ""
echo "==> Files copied. Running migrations..."
python manage.py migrate

echo ""
echo "==> Done. Reminder: add the queue url to dashboard/urls.py if not already present:"
echo "    path('queue/', views.queue, name='queue'),  # queue page (GET)"
echo "    path('queue/status/', views.queue_status, name='queue_status'),  # JSON poll endpoint (GET)"
