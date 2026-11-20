import { x25519 } from "@noble/curves/ed25519";
import { hkdf } from "@noble/hashes/hkdf.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { randomBytes } from "@noble/hashes/utils.js";

/**
 * E2E channel for the relay transport.
 *
 * The browser generates an ephemeral X25519 keypair, derives an AES-256-GCM
 * key with HKDF-SHA256, and wraps every request/event in a counter + timestamp
 * envelope. The relay only sees base64 ciphertext.
 */

const INFO = new TextEncoder().encode("eb-e2e-v1");
const AAD_PREFIX = new TextEncoder().encode("eb-e2e-v1");
const MAX_CLOCK_SKEW_S = 120;

export interface Envelope {
  n: number;
  iv: string;
  ct: string;
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Copy to a plain ArrayBuffer, which WebCrypto's TS types require. */
function asBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

function concat(...arrays: Uint8Array[]): Uint8Array {
  const length = arrays.reduce((total, item) => total + item.length, 0);
  const result = new Uint8Array(length);
  let offset = 0;
  for (const item of arrays) {
    result.set(item, offset);
    offset += item.length;
  }
  return result;
}

function counterBytes(counter: number): Uint8Array {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setUint32(0, counter, false);
  return bytes;
}

export function generateKeyPair(): { privateKey: Uint8Array; publicKey: Uint8Array } {
  const privateKey = randomBytes(32);
  return { privateKey, publicKey: x25519.getPublicKey(privateKey) };
}

export function deriveKey(
  privateKey: Uint8Array,
  peerPublicKey: Uint8Array,
  salt: Uint8Array = new Uint8Array(0)
): Uint8Array {
  const shared = x25519.getSharedSecret(privateKey, peerPublicKey);
  return hkdf(sha256, shared, salt, INFO, 32);
}

export class SecureChannel {
  private key: Uint8Array;
  private sendCounter = 0;
  private recvCounter = -1;
  private cryptoKey: CryptoKey | null = null;

  constructor(key: Uint8Array) {
    if (key.length !== 32) throw new Error("AES-256-GCM key must be 32 bytes");
    this.key = key;
  }

  private async aes(): Promise<CryptoKey> {
    if (!this.cryptoKey) {
      this.cryptoKey = await crypto.subtle.importKey(
        "raw",
        asBuffer(this.key),
        { name: "AES-GCM" },
        false,
        ["encrypt", "decrypt"]
      );
    }
    return this.cryptoKey;
  }

  async seal(payload: unknown, ts = Date.now() / 1000): Promise<Envelope> {
    this.sendCounter += 1;
    const plaintext = new TextEncoder().encode(
      JSON.stringify({ n: this.sendCounter, ts, plain: payload })
    );
    const nonce = concat(counterBytes(this.sendCounter), randomBytes(8));
    const aad = concat(AAD_PREFIX, counterBytes(this.sendCounter));
    const ciphertext = new Uint8Array(
      await crypto.subtle.encrypt(
        { name: "AES-GCM", iv: asBuffer(nonce), additionalData: asBuffer(aad) },
        await this.aes(),
        asBuffer(plaintext)
      )
    );
    return {
      n: this.sendCounter,
      iv: toBase64(nonce),
      ct: toBase64(ciphertext),
    };
  }

  async open(envelope: Envelope): Promise<unknown> {
    const counter = Number(envelope.n);
    if (!Number.isInteger(counter) || counter <= this.recvCounter) {
      throw new Error("replayed counter");
    }
    const nonce = fromBase64(envelope.iv);
    if (nonce.length !== 12) throw new Error("invalid nonce length");
    const aad = concat(AAD_PREFIX, counterBytes(counter));
    let plaintext: ArrayBuffer;
    try {
      plaintext = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: asBuffer(nonce), additionalData: asBuffer(aad) },
        await this.aes(),
        asBuffer(fromBase64(envelope.ct))
      );
    } catch {
      throw new Error("authentication failed");
    }
    let parsed: { ts?: number; plain?: unknown };
    try {
      parsed = JSON.parse(new TextDecoder().decode(plaintext));
    } catch {
      throw new Error("invalid plaintext");
    }
    const ts = Number(parsed.ts ?? 0);
    if (ts && Math.abs(Date.now() / 1000 - ts) > MAX_CLOCK_SKEW_S) {
      throw new Error("stale message");
    }
    this.recvCounter = counter;
    return parsed.plain;
  }
}

export function hex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function unhex(value: string): Uint8Array {
  const result = new Uint8Array(value.length / 2);
  for (let i = 0; i < result.length; i += 1) {
    result[i] = Number.parseInt(value.slice(i * 2, i * 2 + 2), 16);
  }
  return result;
}
