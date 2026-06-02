#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

if [ "$CREATE_DEPLOY_ADMIN" = "True" ]; then
  python manage.py ensure_deploy_admin
fi

if [ "$SEED_DEMO_DATA" = "True" ]; then
  python manage.py seed_demo_data
fi
