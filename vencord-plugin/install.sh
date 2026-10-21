#!/usr/bin/env bash
# Builds Vencord with the SecretCrypt plugin and patches Discord. Linux/macOS.
# Usage: GDPR_PUBKEY=age1... ./install.sh [target-dir]   (default ~/Vencord-secretcrypt)
# The key comes from the init step (gdpr_age_key.pub); asked for if not set. SKIP_INJECT=1 builds only.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-$HOME/Vencord-secretcrypt}"
for t in git node npm; do command -v "$t" >/dev/null || { echo "missing: $t"; exit 1; }; done
AGE_VERSION=0.3.1
KEY="${GDPR_PUBKEY:-}"
[ -z "$KEY" ] && [ -f "$HERE/../gdpr_age_key.pub" ] && KEY="$(cat "$HERE/../gdpr_age_key.pub")"
[ -z "$KEY" ] && read -rp "GDPR public key (age1..., printed by the init step): " KEY
KEY="$(echo "$KEY" | tr -d '[:space:]')"
[[ "$KEY" =~ ^age1[0-9a-z]{50,}$ ]] || { echo "not an age public key: $KEY"; exit 1; }
if command -v pnpm >/dev/null; then PNPM=(pnpm); else PNPM=(npx --yes pnpm); fi

[ -d "$DEST/.git" ] || git clone --depth 1 https://github.com/Vendicated/Vencord.git "$DEST"
cd "$DEST"
git checkout -q -- package.json pnpm-lock.yaml 2>/dev/null || true   # undo our own edit from a previous run so git pull works
"${PNPM[@]}" install --frozen-lockfile
"${PNPM[@]}" add "age-encryption@$AGE_VERSION" -w
mkdir -p src/userplugins
rm -rf src/userplugins/secretCrypt
cp -r "$HERE/secretCrypt" src/userplugins/secretCrypt
sed -i.bak "s|default: \"\" // GDPR_PUBKEY|default: \"$KEY\" // GDPR_PUBKEY|" src/userplugins/secretCrypt/index.ts
rm -f src/userplugins/secretCrypt/index.ts.bak
grep -q "$KEY" src/userplugins/secretCrypt/index.ts || { echo "could not set the key"; exit 1; }
"${PNPM[@]}" build
if [ "${SKIP_INJECT:-0}" = 1 ]; then echo "built; skipping inject"; exit 0; fi
"${PNPM[@]}" inject   # asks which Discord to patch (may need sudo for /usr/share/discord)
echo
echo "Done. Restart Discord, enable SecretCrypt in Settings > Vencord > Plugins."
echo "Every /secret must show the green 'Secret encrypted, sending' toast."
echo "Update later: cd $DEST && git pull && re-run this script. Re-run after Discord updates too."
