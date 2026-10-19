#!/usr/bin/env bash
# Task 235: the board's new stages, model names, planned date and block tools,
# deployed to hedburgaren. Chrille runs this on plastdesk:
#   bash ~/dev/projects/agent-kanban/scripts/deploy-235.sh
# Order: the branch pushed, a dump of the board database and a tar of the
# running code, the committed tree copied in, the app rebuilt (migrations run
# at start: 0006 planned date and block fields, 0007 model names), checked.
# Also pushes the rulebook (agent-config) so every machine reads the new rules.
set -euo pipefail

R=/home/chrille/dev/projects/agent-kanban
BRANCH=task-235-board-stages
D=/srv/containers/agent-kanban
STAMP=$(date +%Y%m%d-%H%M)
remote() { ssh hedburgaren "$1" </dev/null; }
say() { printf '\n== %s\n' "$*"; }

say "1. Grenen ren och pushad"
[ "$(git -C "$R" branch --show-current)" = "$BRANCH" ] || { echo "STOPP: inte på $BRANCH"; exit 1; }
[ -z "$(git -C "$R" status --porcelain --untracked-files=no)" ] || { echo "STOPP: ocommittade ändringar"; exit 1; }
git -C "$R" push github "$BRANCH"
HEAD_SHORT=$(git -C "$R" rev-parse --short HEAD)

say "2. Säkerhetskopia av tavlans databas och koden som körs"
remote "mkdir -p $D/backups"
remote "cd $D && URL=\$(grep '^KANBAN_DATABASE_URL=' .env | cut -d= -f2- | sed 's#postgresql+asyncpg://#postgresql://#') && docker run --rm --network arc-pg-net postgres:17 pg_dump \"\$URL\" -Fc > backups/kanban-$STAMP-before-235.dump && ls -la backups/kanban-$STAMP-before-235.dump"
ENTRIES=$(remote "cd $D && docker run --rm -i postgres:17 pg_restore -l < backups/kanban-$STAMP-before-235.dump | wc -l")
echo "pg_restore -l: $ENTRIES poster"
[ "${ENTRIES:-0}" -ge 20 ] || { echo "STOPP: säkerhetskopian ser tom ut. Inget är ändrat."; exit 1; }
remote "cd $D && tar -czf backups/code-$STAMP-before-235.tar.gz src web migrations tests README.md README.ru.md pyproject.toml uv.lock Dockerfile alembic.ini"

say "3. Koden till hedburgaren (commit $HEAD_SHORT)"
LOCAL=$(mktemp -d)
git -C "$R" archive HEAD | tar -x -C "$LOCAL"
for dir in src web migrations tests; do
  rsync -a --delete --exclude node_modules --exclude dist "$LOCAL/$dir/" "hedburgaren:$D/$dir/"
done
rsync -a "$LOCAL/README.md" "$LOCAL/README.ru.md" "$LOCAL/pyproject.toml" "$LOCAL/uv.lock" "$LOCAL/Dockerfile" "$LOCAL/alembic.ini" "hedburgaren:$D/"

say "4. Appen byggs om och startas (migreringarna körs vid start)"
remote "cd $D && docker compose up -d --build --no-deps app"
sleep 15
remote "docker logs --since 2m agent-kanban-app-1 2>&1 | grep -iE 'alembic|migrat|error|traceback' | tail -15"
CODE=$(remote "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7331/")
echo "Tavlan svarar: $CODE"
if [ "$CODE" != 200 ]; then
  echo "VARNING: tavlan svarar inte 200. Återställning: koden ur backups/code-$STAMP-before-235.tar.gz och databasen ur backups/kanban-$STAMP-before-235.dump. Säg till Klådan."
  exit 1
fi

say "5. Regelboken till alla maskiner"
git -C /home/chrille/agent-config push origin main

say "KLART, tavlan på $HEAD_SHORT. Säkerhetskopior i hedburgaren:$D/backups/"
