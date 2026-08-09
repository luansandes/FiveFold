import { send } from "@vercel/queue";

type PublishPayload = { jobIds?: unknown };

export default async function handler(request: Request): Promise<Response> {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }
  if (request.headers.get("authorization") !== `Bearer ${process.env.CRON_SECRET}`) {
    return new Response("Unauthorized", { status: 401 });
  }
  const payload = (await request.json()) as PublishPayload;
  if (!Array.isArray(payload.jobIds) || payload.jobIds.some((id) => typeof id !== "string")) {
    return Response.json({ error: "jobIds must be an array of strings" }, { status: 422 });
  }
  const topic = process.env.QUEUE_TOPIC ?? "fivefold-stage-ready";
  const published = await Promise.all(
    payload.jobIds.map(async (jobId) => {
      const result = await send(topic, { jobId }, { idempotencyKey: jobId });
      return result.messageId;
    }),
  );
  return Response.json({ published });
}
