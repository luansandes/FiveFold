import { handleCallback, send } from "@vercel/queue";

type StageReadyMessage = { jobId: string };
type JobResult = { status: string; next_job_id?: string | null; error?: string };

export const POST = handleCallback(
  async (message: StageReadyMessage, metadata) => {
    if (!message?.jobId || typeof message.jobId !== "string") {
      throw new Error("Queue message is missing jobId");
    }
    console.log(JSON.stringify({
      event: "queue.message_received",
      jobId: message.jobId,
      messageId: metadata.messageId,
      deliveryCount: metadata.deliveryCount,
    }));
    const deploymentHost = process.env.VERCEL_URL;
    if (!deploymentHost || !process.env.CRON_SECRET) {
      throw new Error("Queue consumer deployment configuration is incomplete");
    }
    console.log(JSON.stringify({ event: "queue.processor_invoked", jobId: message.jobId }));
    const response = await fetch(
      `https://${deploymentHost}/api/internal/jobs/${encodeURIComponent(message.jobId)}/process`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${process.env.CRON_SECRET}` },
      },
    );
    if (!response.ok) {
      console.error(JSON.stringify({
        event: "queue.processor_http_error",
        jobId: message.jobId,
        status: response.status,
      }));
      throw new Error(`Stage processor returned HTTP ${response.status}`);
    }
    const result = (await response.json()) as JobResult;
    if (["retrying", "not_ready"].includes(result.status)) {
      console.warn(JSON.stringify({
        event: "queue.processor_retry",
        jobId: message.jobId,
        status: result.status,
        error: result.error,
      }));
      throw new Error(result.error ?? `Stage processor returned ${result.status}`);
    }
    console.log(JSON.stringify({
      event: "queue.processor_completed",
      jobId: message.jobId,
      status: result.status,
      nextJobId: result.next_job_id ?? null,
    }));
    if (result.next_job_id) {
      const topic = process.env.QUEUE_TOPIC ?? "fivefold-stage-ready";
      const published = await send(
        topic,
        { jobId: result.next_job_id },
        { idempotencyKey: result.next_job_id },
      );
      console.log(JSON.stringify({
        event: "queue.successor_published",
        jobId: result.next_job_id,
        messageId: published.messageId,
      }));
    }
  },
  {
    visibilityTimeoutSeconds: 600,
    retry: (error, metadata) => {
      console.warn(JSON.stringify({
        event: "queue.delivery_retry",
        deliveryCount: metadata.deliveryCount,
        error: error instanceof Error ? error.message : String(error),
      }));
      if (metadata.deliveryCount > 5) return { acknowledge: true };
      return { afterSeconds: Math.min(300, 2 ** metadata.deliveryCount * 5) };
    },
  },
);
