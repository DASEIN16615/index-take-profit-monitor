/**
 * Cloudflare Worker: 准时可靠触发 GitHub Actions 监控工作流
 * 解决 GitHub Actions 原生 schedule cron 严重延迟与静默丢弃的问题
 */

export default {
  // 定时触发器 (Cloudflare Cron Triggers)
  async scheduled(event, env, ctx) {
    const cron = event.cron;
    let targetWorkflow = "daily.yml";

    // UTC 06:28 (北京 14:28) 触发 ETF 轮动盘中决策监控
    if (cron === "28 6 * * 1-5" || (cron && cron.includes(" 6 "))) {
      targetWorkflow = "etf_rotation_daily.yml";
    } else if (cron === "0 1 * * 1-5" || (cron && cron.includes(" 1 "))) {
      targetWorkflow = "daily.yml";
    }

    console.log(`Cron fired: ${cron} -> Triggering workflow: ${targetWorkflow}`);
    return dispatch(env, targetWorkflow);
  },

  // HTTP 测试/手动触发入口:
  // GET /?key=<KEY>&workflow=etf_rotation_daily.yml
  // GET /?key=<KEY>&workflow=daily.yml
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 如果配置了安全密钥 KEY，则做校验；未配置则跳过
    if (env.KEY && url.searchParams.get("key") !== env.KEY) {
      return new Response(JSON.stringify({ error: "Forbidden: invalid key" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      });
    }

    // 默认触发 etf_rotation_daily.yml 或由 query 指定
    const targetWorkflow =
      url.searchParams.get("workflow") || "etf_rotation_daily.yml";

    const result = await dispatch(env, targetWorkflow);
    return result;
  },
};

async function dispatch(env, workflowFile) {
  const repo = env.REPO || "DASEIN16615/index-take-profit-monitor";
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflowFile}/dispatches`;

  if (!env.GH_TOKEN) {
    return new Response(
      JSON.stringify({ error: "Missing GH_TOKEN secret in environment" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }

  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "cloudflare-worker-actions-trigger",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });

  if (!resp.ok) {
    const errText = await resp.text();
    console.error(`Dispatch failed: ${resp.status} ${errText}`);
    return new Response(
      JSON.stringify({
        ok: false,
        workflow: workflowFile,
        status: resp.status,
        error: errText,
      }),
      { status: 502, headers: { "Content-Type": "application/json" } }
    );
  }

  console.log(`Successfully dispatched ${workflowFile}`);
  return new Response(
    JSON.stringify({
      ok: true,
      repo,
      workflow: workflowFile,
      message: "Successfully triggered GitHub Actions workflow",
      timestamp: new Date().toISOString(),
    }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
}
