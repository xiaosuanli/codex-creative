import { homedir } from 'node:os';
import { join } from 'node:path';
import { DirectorConnect } from './index.mjs';
import { fileStore } from './file-store.mjs';
import { readCodexContext } from './codex-context.mjs';

// The CLI carries OAuth setup and discovery. Business writes need the host's
// trusted conversation/message adapter, not model-invented message identities.
export async function runCommand(connect, command, input = {}, context) {
  switch (command) {
    case 'auth-start': return connect.beginLoginPersisted();
    case 'auth-complete': {
      const status = await connect.completeLogin(input);
      return { ...status, initialization: await connect.projects(), next: '让用户选择工程，再调用 chapters 列出章节' };
    }
    case 'capabilities': return connect.capabilities();
    case 'context': return connect.conversation(context.conversationId);
    case 'open-chapter': {
      await connect.openChapter(context.conversationId, input.chapterId, input.projectId);
      await connect.beginUserTurn(context.conversationId, context.userTurnId);
      return connect.conversation(context.conversationId);
    }
    case 'create-project': return connect.create('project', input, context);
    case 'create-chapter': return connect.create('chapter', input, context);
    case 'execute': {
      if (Object.keys(input).some(key => !['operation', 'arguments'].includes(key))) throw Error('业务请求不能覆盖宿主身份');
      await connect.beginUserTurn(context.conversationId, context.userTurnId);
      return connect.execute(input.operation, input.arguments, context);
    }
    case 'status': return connect.status();
    case 'account': return connect.account();
    case 'projects': return connect.projects();
    case 'chapters': return connect.chapters(input.projectId);
    case 'logout': return connect.disconnect();
    default: throw Error('支持 auth-start/auth-complete/status/account/projects/chapters/capabilities/context/open-chapter/create-project/create-chapter/execute/logout');
  }
}

export function createClient(host) {
  if (host !== 'codex') throw Error('请指定 codex 宿主');
  const store = fileStore(process.env.XSL_DIRECTOR_STATE_DIR || join(homedir(), '.config', 'xiaosuanli-director', host));
  const connect = new DirectorConnect({ client: { id: 'xiaosuanli-' + host, channel: host },
    apiOrigin: process.env.XSL_DIRECTOR_API_ORIGIN || 'https://api.xiaosuanli.com',
    siteOrigin: process.env.XSL_DIRECTOR_SITE_ORIGIN || 'https://www.xiaosuanli.com', store });
  return { connect, store };
}

export async function main() {
  const [host, command] = process.argv.slice(2);
  const { connect, store } = createClient(host);
  let context;
  if (['context', 'open-chapter', 'create-project', 'create-chapter', 'execute'].includes(command)) {
    context = await readCodexContext(store, process.env.XSL_DIRECTOR_CONTEXT_KEY);
  }
  let input = {};
  if (['auth-complete', 'chapters', 'open-chapter', 'create-project', 'create-chapter', 'execute'].includes(command)) {
    let raw = '';
    for await (const part of process.stdin) { raw += part; if (raw.length > 2 * 1024 * 1024) throw Error('输入过长，请选择单章范围'); }
    input = JSON.parse(raw);
  }
  console.log(JSON.stringify(await runCommand(connect, command, input, context)));
}
