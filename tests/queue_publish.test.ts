import assert from "node:assert/strict";
import test from "node:test";

import type { VercelRequest, VercelResponse } from "@vercel/node";

import { publishStageJobs } from "../api/queue_publish";

type ResponseCapture = {
  statusCode: number;
  body: unknown;
  response: VercelResponse;
};

function captureResponse(): ResponseCapture {
  const capture = { statusCode: 0, body: undefined as unknown };
  const response = {
    status(code: number) {
      capture.statusCode = code;
      return response;
    },
    json(body: unknown) {
      capture.body = body;
      return response;
    },
  } as unknown as VercelResponse;
  return {
    get statusCode() {
      return capture.statusCode;
    },
    get body() {
      return capture.body;
    },
    response,
  };
}

test("publishes every job with its database ID as the idempotency key", async () => {
  process.env.CRON_SECRET = "queue-test-secret";
  process.env.QUEUE_TOPIC = "test-stage-ready";
  const request = {
    method: "POST",
    headers: { authorization: "Bearer queue-test-secret" },
    body: { jobIds: ["job-one", "job-two"] },
  } as VercelRequest;
  const capture = captureResponse();
  const calls: Array<{ topic: string; jobId: string; idempotencyKey: string }> = [];

  await publishStageJobs(request, capture.response, async (topic, payload, options) => {
    calls.push({ topic, jobId: payload.jobId, idempotencyKey: options.idempotencyKey });
    return { messageId: `message-${payload.jobId}` };
  });

  assert.equal(capture.statusCode, 200);
  assert.deepEqual(capture.body, { published: ["message-job-one", "message-job-two"] });
  assert.deepEqual(calls, [
    { topic: "test-stage-ready", jobId: "job-one", idempotencyKey: "job-one" },
    { topic: "test-stage-ready", jobId: "job-two", idempotencyKey: "job-two" },
  ]);
});

test("rejects an invalid worker secret without publishing", async () => {
  process.env.CRON_SECRET = "queue-test-secret";
  const request = {
    method: "POST",
    headers: { authorization: "Bearer incorrect" },
    body: { jobIds: ["job-one"] },
  } as VercelRequest;
  const capture = captureResponse();
  let calls = 0;

  await publishStageJobs(request, capture.response, async () => {
    calls += 1;
    return { messageId: "unexpected" };
  });

  assert.equal(capture.statusCode, 401);
  assert.deepEqual(capture.body, { error: "Unauthorized" });
  assert.equal(calls, 0);
});
