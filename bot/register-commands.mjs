// 註冊 /watchlist 斜線指令（選配）。用法：
//   DISCORD_APPLICATION_ID=... DISCORD_BOT_TOKEN=... node register-commands.mjs
// 金鑰只從環境變數讀，不會印出。可加 DISCORD_GUILD_ID 只註冊到單一伺服器（立即生效）。
const appId = process.env.DISCORD_APPLICATION_ID;
const token = process.env.DISCORD_BOT_TOKEN;
const guild = process.env.DISCORD_GUILD_ID;
if (!appId || !token) {
  console.error("請先設定環境變數 DISCORD_APPLICATION_ID 與 DISCORD_BOT_TOKEN");
  process.exit(1);
}
const url = `https://discord.com/api/v10/applications/${appId}/` + (guild ? `guilds/${guild}/` : "") + "commands";
const commands = [{ name: "watchlist", description: "列出目前關注清單（只有你看得到）", type: 1 }];
const res = await fetch(url, {
  method: "PUT",
  headers: { Authorization: `Bot ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify(commands),
});
if (!res.ok) {
  console.error(`註冊失敗：HTTP ${res.status}`);
  process.exit(1);
}
console.log(`已註冊 ${commands.length} 個指令${guild ? "（單一伺服器）" : "（全域，最久約 1 小時生效）"}`);
