# SecretCrypt

Vencord userplugin for GDPR's `/secret`. It encrypts the form value with age before the
request leaves your Discord client. The value is gzipped first and sent with a `WSEC2:` prefix.

It fails closed: if anything goes wrong, the submit is cancelled and nothing is sent.

You can drop a small text file (for example a .pem, max 16 KB) onto the Value box. It is read
on your computer and nothing is uploaded.

It only touches form submits whose custom_id starts with `wsec:`. GDPR's form uses `wsec:v1`,
`wsec:name` and `wsec:value`. Everything else passes through unchanged.

## Install

See "Optional: encrypted /secret" in the main README. The install script:

1. clones Vencord to `~/Vencord-secretcrypt`
2. adds this plugin with your public key, and `age-encryption`
3. builds it
4. runs `pnpm inject`, which asks which Discord install to patch

Use a normal Discord install. Flatpak and Snap are read-only, and a .deb install needs sudo for
the inject step. If you already use Vencord, this replaces it with this build; your other plugins
and settings stay.

You can change the public key later in the plugin settings. If the key is empty or invalid, the
plugin refuses to send.
