import { send } from "@vercel/queue";
import type { VercelRequest, VercelResponse } from "@vercel/node";

type PublishPayload = { jobIds?: unknown };
type QueueSender = (
  topic: string,
  payload: { jobId: string },
  options: { idempotencyKey: string },
) => Promise<{ messageId: string }>;

export async function publishStageJobs(
  request: VercelRequest,
  response: VercelResponse,
  sender: QueueSender = send,
): Promise<void> {
  if (request.method !== "POST") {
    response.status(405).json({ error: "Method not allowed" });
    return;
  }
  if (request.headers.authorization !== `Bearer ${process.env.CRON_SECRET}`) {
    response.status(401).json({ error: "Unauthorized" });
    return;
  }
  let payload: PublishPayload;
  try {
    payload = (typeof request.body === "string"
      ? JSON.parse(request.body)
      : request.body) as PublishPayload;
  } catch {
    response.status(400).json({ error: "Request body must be valid JSON" });
    return;
  }
  if (!Array.isArray(payload.jobIds) || payload.jobIds.some((id) => typeof id !== "string")) {
    response.status(422).json({ error: "jobIds must be an array of strings" });
    return;
  }
  const topic = process.env.QUEUE_TOPIC ?? "fivefold-stage-ready";
  const published = await Promise.all(
    payload.jobIds.map(async (jobId) => {
      const result = await sender(topic, { jobId }, { idempotencyKey: jobId });
      return result.messageId;
    }),
  );
  response.status(200).json({ published });
}

export default async function handler(
  request: VercelRequest,
  response: VercelResponse,
): Promise<void> {
  await publishStageJobs(request, response);
}
