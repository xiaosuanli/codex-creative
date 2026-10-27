import { createHash } from 'node:crypto';
import { bindCodexCommand } from './codex-shell.mjs';

export const contextKey = (conversation, turn) => 'codex-turn-' + createHash('sha256').update(conversation + '/' + turn).digest('hex');
const identity = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);

export async function codexHook(input, { connect, store, platform = process.platform }) {
  const conversationId = input.agent_id || input.session_id;
  if (!identity(conversationId) || !identity(input.turn_id)) throw Error('Codex 未提供有效对话和消息标识');
  const key = contextKey(conversationId, input.turn_id);
  if (input.hook_event_name === 'UserPromptSubmit') {
    // Do not persist the prompt or read the transcript. Retry preserves identity.
    await store.set(key, { conversationId, userTurnId: input.turn_id });
    try { await connect.beginUserTurn(conversationId, input.turn_id); }
    catch (error) {
      if (!['CHAPTER_NOT_OPEN', 'LOGIN_REQUIRED'].includes(error.code)) return { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: '小算狸消息同步暂不可用；业务调用前需要恢复连接，其它任务可继续。' } };
    }
    return { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: '小算狸导演台已记录本条用户消息。业务调用使用随包 CLI；不要自行填写对话或消息身份。' } };
  }
  if (input.hook_event_name !== 'PreToolUse') return {};
  // Hosts may normalize shell calls to Bash/command or expose exec_command/cmd.
  // Rewrite the same field the host supplied, preserving all other arguments.
  const commandField = input.tool_name === 'Bash' ? 'command' : input.tool_name === 'exec_command' ? 'cmd' : null;
  if (!commandField) return {};
  const command = input.tool_input?.[commandField];
  if (typeof command !== 'string' || !command.replaceAll('\\', '/').includes('scripts/connect.mjs')) return {};
  const turn = await store.get(key);
  if (!turn || turn.conversationId !== conversationId || turn.userTurnId !== input.turn_id) {
    return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: '请先启用小算狸消息钩子，再发送一条新消息。不能为旧请求借用其它消息身份。' } };
  }
  // Bind this invocation to its original turn, even if another message arrives
  // before the shell starts. Only this command's subprocess receives the key.
  return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'allow',
    updatedInput: { ...input.tool_input, [commandField]: bindCodexCommand(command, key, platform) } } };
}

export async function readCodexContext(store, key) {
  if (typeof key !== 'string' || !/^codex-turn-[a-f0-9]{64}$/.test(key)) throw Error('缺少 Codex 消息接线；请完成安装、信任钩子并发送新消息');
  const value = await store.get(key);
  if (!value || !identity(value.conversationId) || !identity(value.userTurnId) || contextKey(value.conversationId, value.userTurnId) !== key) throw Error('Codex 消息上下文无效');
  return value;
}
