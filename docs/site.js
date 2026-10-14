'use strict';
const normalized = value => value.replace(/\r\n?/g, '\n').trim();
document.getElementById('preview').addEventListener('click', () => {
  const match = normalized(document.getElementById('match').value);
  const message = normalized(document.getElementById('sample').value);
  const reply = document.getElementById('reply').value;
  const mode = document.getElementById('mode').value;
  const matched = Boolean(match && (mode === 'exact' ? message === match : message.includes(match)));
  const valid = Boolean(reply.trim() && [...reply].length <= 100 && !/\p{C}/u.test(reply));
  const result = document.getElementById('result');
  result.classList.toggle('miss', !matched || !valid);
  result.textContent = !valid ? '请填写 1–100 字符的单行回复内容。' : matched ? `匹配成功 · 将回复：${reply}。此演示不连接 Telegram，不发送任何消息。` : '未匹配，不会回复。你可以修改待识别消息或匹配方式，再预览。';
});
