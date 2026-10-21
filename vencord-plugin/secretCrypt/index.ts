/*
 * SecretCrypt: encrypts the /secret modal value in the client (age, X25519 recipient)
 * before the interaction request leaves, so Discord's servers only see ciphertext.
 * Fails closed: any problem cancels the submit; plaintext is never sent as a fallback.
 */
import definePlugin, { OptionType } from "@utils/types";
import { definePluginSettings } from "@api/Settings";
import { RestAPI, showToast } from "@webpack/common";
import { Encrypter } from "age-encryption";

const NS = "wsec:";               // GDPR sets fixed custom_ids wsec:v1 / wsec:name / wsec:value
const VALUE_ID = "wsec:value";
const PREFIX = "WSEC2:";           // WSEC2 = base64(age(gzip(text))); GDPR also still accepts WSEC1 (no gzip)
const DROP_MAX = 16384;            // max dropped file size in bytes
const MODAL_TITLE = "Store a secret";
const FIELD_MAX = 4000;           // Discord modal paragraph limit
const SIZE_LIMIT_MSG = "Secret too long to encrypt into one field";

const settings = definePluginSettings({
    publicKey: {
        type: OptionType.STRING,
        description: "GDPR age public key (age1...)",
        default: "" // GDPR_PUBKEY: filled in by install.sh / install.ps1
    }
});

function toast(msg: string, kind: "success" | "failure") {
    showToast(msg, kind);
}

async function gzip(text: string): Promise<Uint8Array> {
    const cs = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
    return new Uint8Array(await new Response(cs).arrayBuffer());
}

function b64(bytes: Uint8Array) {
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    return btoa(s);
}

export async function encryptValue(plain: string, publicKey: string): Promise<string> {
    if (!/^age1[0-9a-z]{50,}$/.test(publicKey)) throw new Error("No valid GDPR public key configured");
    const e = new Encrypter();
    e.addRecipient(publicKey);
    const out = PREFIX + b64(await e.encrypt(await gzip(plain)));
    if (out.length > FIELD_MAX) throw new Error(SIZE_LIMIT_MSG);
    return out;
}

function findValueField(body: any): any | null {
    for (const row of body?.data?.components ?? [])
        for (const c of row?.components ?? [])
            if (c?.custom_id === VALUE_ID) return c;
    return null;
}

/** Insert text into a React-controlled textarea so the modal state sees it. */
function setTextareaValue(ta: HTMLTextAreaElement, text: string) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(ta, text);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
}

function secretTextarea(target: EventTarget | null): HTMLTextAreaElement | null {
    const el = target as HTMLElement | null;
    const ta = el?.closest?.("textarea") as HTMLTextAreaElement | null;
    if (!ta) return null;
    const dialog = ta.closest("[role=dialog]");
    return dialog && (dialog.textContent ?? "").includes(MODAL_TITLE) ? ta : null;
}

// Drag-and-drop a text file (e.g. .pem) onto the /secret value box: read locally, paste the
// text into the box. Nothing is uploaded; the normal encrypt-on-submit path then runs.
function onDragOver(e: DragEvent) {
    if (secretTextarea(e.target) && e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
        e.stopImmediatePropagation();
    }
}

async function onDrop(e: DragEvent) {
    const ta = secretTextarea(e.target);
    const file = e.dataTransfer?.files?.[0];
    if (!ta || !file) return;
    e.preventDefault();
    e.stopImmediatePropagation(); // keep Discord from uploading the file as an attachment
    try {
        if (file.size > DROP_MAX) throw new Error("file too big (max 16 KB)");
        const text = await file.text();
        if (text.includes("\u0000")) throw new Error("not a text file");
        setTextareaValue(ta, text);
        toast(`Loaded ${file.name} locally (not uploaded)`, "success");
    } catch (err: any) {
        toast(`Drop failed: ${err?.message ?? err}`, "failure");
    }
}

let origPost: any;

export default definePlugin({
    name: "SecretCrypt",
    description: "Encrypts the GDPR /secret modal value client-side before it is sent",
    authors: [{ name: "GDPR", id: 0n }],
    settings,

    start() {
        document.addEventListener("dragover", onDragOver, true);
        document.addEventListener("drop", onDrop, true);
        origPost = RestAPI.post;
        RestAPI.post = async function (req: any, ...rest: any[]) {
            const body = req?.body;
            // Broad match on purpose: any interaction POST that touches a wsec: id is treated as a
            // secret submit, so a changed shape cancels instead of leaking plaintext.
            const isSecret = typeof req?.url === "string" && req.url.endsWith("/interactions")
                && (String(body?.data?.custom_id ?? "").startsWith(NS)
                    || (body?.data?.components ?? []).some((r: any) =>
                        (r?.components ?? []).some((c: any) => String(c?.custom_id ?? "").startsWith(NS))));
            if (isSecret) {
                try {
                    const field = findValueField(body);
                    if (!field || typeof field.value !== "string") throw new Error("Value field not found");
                    field.value = await encryptValue(field.value, settings.store.publicKey);
                    toast("Secret encrypted, sending", "success");
                } catch (err: any) {
                    toast(`Secret NOT sent: ${err?.message ?? err}`, "failure");
                    throw err; // fail closed: request is never made
                }
            }
            return origPost.call(this, req, ...rest);
        };
    },

    stop() {
        document.removeEventListener("dragover", onDragOver, true);
        document.removeEventListener("drop", onDrop, true);
        if (origPost) RestAPI.post = origPost;
    }
});
