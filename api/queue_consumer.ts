import { handleCallback, send } from "@vercel/queue";

type StageReadyMessage = { jobId: string };
type JobResult = { status: string; next_job_id?: string | null; error?: string };

export default handleCallback(
  async (message: StageReadyMessage) => {
    if (!message?.jobId || typeof message.jobId !== "string") {
      throw new Error("Queue message is missing jobId");
    }
    const deploymentHost = process.env.VERCEL_URL;
    if (!deploymentHost || !process.env.CRON_SECRET) {
      throw new Error("Queue consumer deployment configuration is incomplete");
    }
    const response = await fetch(
      `https://${deploymentHost}/api/internal/jobs/${encodeURIComponent(message.jobId)}/process`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${process.env.CRON_SECRET}` },
      },
    );
    if (!response.ok) {
      throw new Error(`Stage processor returned HTTP ${response.status}`);
    }
    const result = (await response.json()) as JobResult;
    if (["retrying", "not_ready"].includes(result.status)) {
      throw new Error(result.error ?? `Stage processor returned ${result.status}`);
    }
    if (result.next_job_id) {
      const topic = process.env.QUEUE_TOPIC ?? "fivefold-stage-ready";
      await send(
        topic,
        { jobId: result.next_job_id },
        { idempotencyKey: result.next_job_id },
      );
    }
  },
  {
    visibilityTimeoutSeconds: 600,
    retry: (_error, metadata) => {
      if (metadata.deliveryCount > 5) return { acknowledge: true };
      return { afterSeconds: Math.min(300, 2 ** metadata.deliveryCount * 5) };
    },
  },
);
