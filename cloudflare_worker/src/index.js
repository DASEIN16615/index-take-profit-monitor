export default {
  // 定时触发:工作日 UTC 01:00 = 北京 09:00
  async scheduled(event, env, ctx) {
    return dispatch(env);
  },

  // 手动测试入口:GET ?key=<KEY> 触发一次
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.searchParams.get("key") !== env.KEY) {
      return new Response("forbidden", { status: 403 });
    }
    return dispatch(env);
  },
};

async function dispatch(env) {
  // 仓库从环境变量读取(部署时设置 REPO=OWNER/REPO),避免硬编码
  const repo = env.REPO || "YOUR_GITHUB_USER/YOUR_REPO";
  const url = `https://api.github.com/repos/${repo}/actions/workflows/daily.yml/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + env.GH_TOKEN,
      Accept: "application/vnd.github+json",
      "User-Agent": "index-monitor-trigger",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    return new Response("dispatch failed: " + resp.status + " " + text, {
      status: 502,
    });
  }
  return new Response("dispatched ok", { status: 200 });
}
