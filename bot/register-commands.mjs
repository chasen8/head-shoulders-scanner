// 註冊斜線指令 /watchlist、/notify（D35）。用法：
//   DISCORD_APPLICATION_ID=... DISCORD_BOT_TOKEN=... node register-commands.mjs
// 金鑰只從環境變數讀，不會印出。可加 DISCORD_GUILD_ID 只註冊到單一伺服器（立即生效）。
// GitHub Actions 的 "Discord channel IDs (setup)" workflow 也會執行這支（register_commands 步驟）。
const appId = process.env.DISCORD_APPLICATION_ID;
const token = process.env.DISCORD_BOT_TOKEN;
const guild = process.env.DISCORD_GUILD_ID;
if (!appId || !token) {
  console.error("請先設定環境變數 DISCORD_APPLICATION_ID 與 DISCORD_BOT_TOKEN");
  process.exit(1);
}
if (!/^\d{5,25}$/.test(appId) || (guild && !/^\d{5,25}$/.test(guild))) {
  console.error("DISCORD_APPLICATION_ID／DISCORD_GUILD_ID 應該是數字 ID");
  process.exit(1);
}
const url = `https://discord.com/api/v10/applications/${appId}/` + (guild ? `guilds/${guild}/` : "") + "commands";
export const commands = [
  { name: "watchlist", description: "列出你的關注清單與通知方式（只有你看得到）", type: 1 },
  {
    name: "notify",
    description: "設定你的關注通知方式",
    type: 1,
    options: [{
      name: "mode",
      description: "通知方式",
      type: 3, // STRING
      required: true,
      choices: [
        { name: "在 #關注 頻道 @我", value: "channel" },
        { name: "機器人私訊", value: "dm" },
      ],
    }],
  },
];
const res = await fetch(url, {
  method: "PUT",
  headers: { Authorization: `Bot ${token}`, "Content-Type": "application/json", "User-Agent": "ihs-setup (register-commands, 1.0)" },
  body: JSON.stringify(commands),
});
if (!res.ok) {
  const body = (await res.text()).slice(0, 300);
  console.error(`註冊失敗：HTTP ${res.status} ${body}`);
  process.exit(1);
}
const done = await res.json();
console.log(`已註冊 ${done.length} 個指令：${done.map((c) => "/" + c.name).join("、")}` +
  (guild ? "（單一伺服器，立即生效）" : "（全域，最久約 1 小時生效）"));
