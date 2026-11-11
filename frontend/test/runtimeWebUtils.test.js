import test from "node:test";
import assert from "node:assert/strict";
import {
  buildRuntimeWebEndpointPayload, runtimeWebEndpointDefaults, webEndpointTarget,
} from "../src/runtimeWebUtils.js";

test("OpenClaw defaults to an SSH tunnel and token authentication", () => {
  const form = runtimeWebEndpointDefaults({ family_code: "openclaw" }, null);
  assert.equal(form.access_mode, "local_ssh_tunnel");
  assert.equal(form.auth_mode, "token");
  assert.equal(form.display_name, "OpenClaw Control UI");
});

test("blank credentials are omitted so edits preserve the encrypted secret", () => {
  const form = runtimeWebEndpointDefaults({ family_code: "openclaw" }, {
    auth_mode: "token", row_version: 4, access_mode: "local_ssh_tunnel",
  });
  const payload = buildRuntimeWebEndpointPayload(form);
  assert.equal(payload.row_version, 4);
  assert.equal(Object.hasOwn(payload, "token"), false);
});

test("Tailscale Serve always uses an HTTPS origin", () => {
  const payload = buildRuntimeWebEndpointPayload({
    ...runtimeWebEndpointDefaults({ family_code: "openclaw" }, null),
    access_mode: "tailscale_serve",
    direct_url: "https://gateway.example.ts.net",
    auth_mode: "none",
  });
  assert.equal(payload.scheme, "https");
  assert.equal(payload.direct_url, "https://gateway.example.ts.net");
  assert.equal(webEndpointTarget({ ...payload, base_path: "/openclaw" }), "https://gateway.example.ts.net/openclaw");
});
