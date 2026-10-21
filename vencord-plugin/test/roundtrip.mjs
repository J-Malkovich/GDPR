import * as age from "age-encryption";
const id = await age.generateIdentity();
const rcp = await age.identityToRecipient(id);
const e = new age.Encrypter(); e.addRecipient(rcp);
const pt = process.argv[2];
const ct = await e.encrypt(pt);
const b64 = Buffer.from(ct).toString("base64");
console.log(JSON.stringify({id, b64, pt}));
