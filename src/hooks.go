package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"unicode/utf16"
)

func quote(s string) string   { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }
func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

const utf8PS = "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)"

func powershell(script string) string {
	words := utf16.Encode([]rune(script))
	b := make([]byte, len(words)*2)
	for i, v := range words {
		b[2*i] = byte(v)
		b[2*i+1] = byte(v >> 8)
	}
	return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + base64.StdEncoding.EncodeToString(b)
}
func hookCommand(exe string, windows bool) string {
	if !windows {
		return quote(exe) + " hook"
	}
	return powershell(utf8PS + "\n[Console]::In.ReadToEnd() | & " + psQuote(exe) + " hook\nexit $LASTEXITCODE")
}
func bindCommand(command, key string, windows bool) string {
	if !windows {
		return "env XSL_DIRECTOR_CONTEXT_KEY=" + quote(key) + " /bin/sh -c " + quote(command)
	}
	return powershell("$ErrorActionPreference = 'Stop'\n" + utf8PS + "\n$env:XSL_DIRECTOR_CONTEXT_KEY = " + psQuote(key) + "\n& {\n" + command + "\n}\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }")
}
func isDirectorHook(h object) bool {
	for _, v := range []string{str(h["command"]), str(h["commandWindows"])} {
		v = strings.ReplaceAll(v, "\\", "/")
		if strings.Contains(v, "xiaosuanli-director/scripts/codex-hook.mjs") || strings.Contains(v, "xiaosuanli-director/bin/xsl-director-") {
			return true
		}
		if strings.HasPrefix(v, "powershell.exe -NoProfile -NonInteractive -EncodedCommand ") {
			raw, e := base64.StdEncoding.DecodeString(strings.TrimPrefix(v, "powershell.exe -NoProfile -NonInteractive -EncodedCommand "))
			if e != nil || len(raw)%2 != 0 {
				continue
			}
			words := make([]uint16, len(raw)/2)
			for i := range words {
				words[i] = uint16(raw[2*i]) | uint16(raw[2*i+1])<<8
			}
			decoded := strings.ReplaceAll(string(utf16.Decode(words)), "\\", "/")
			if strings.Contains(decoded, "xiaosuanli-director/scripts/codex-hook.mjs") || strings.Contains(decoded, "xiaosuanli-director/bin/xsl-director-") {
				return true
			}
		}
	}
	return false
}
func setupHooks() (object, error) {
	home, e := os.UserHomeDir()
	if e != nil {
		return nil, e
	}
	home = env("CODEX_HOME", filepath.Join(home, ".codex"))
	p := filepath.Join(home, "hooks.json")
	config := object{}
	b, e := os.ReadFile(p)
	if e != nil && !os.IsNotExist(e) {
		return nil, e
	}
	if e == nil {
		if e = json.Unmarshal(b, &config); e != nil || config == nil {
			return nil, errors.New("Codex 钩子配置不可读取")
		}
	}
	before := string(jsonBytes(config))
	hooks := obj(config["hooks"])
	if hooks == nil {
		hooks = object{}
		config["hooks"] = hooks
	}
	exe, e := os.Executable()
	if e != nil {
		return nil, e
	}
	exe, e = filepath.Abs(exe)
	if e != nil {
		return nil, e
	}
	for _, event := range []string{"UserPromptSubmit", "PreToolUse"} {
		groups := []any{}
		existing, ok := hooks[event].([]any)
		if hooks[event] != nil && !ok {
			return nil, errors.New("Codex 钩子配置格式错误")
		}
		for _, item := range existing {
			group := obj(item)
			if group == nil {
				return nil, errors.New("Codex 钩子配置格式错误")
			}
			list, ok := group["hooks"].([]any)
			if !ok {
				return nil, errors.New("Codex 钩子配置格式错误")
			}
			kept := []any{}
			for _, v := range list {
				h := obj(v)
				if h == nil {
					return nil, errors.New("Codex 钩子配置格式错误")
				}
				if !isDirectorHook(h) {
					kept = append(kept, h)
				}
			}
			if len(kept) > 0 {
				g := clone(group)
				g["hooks"] = kept
				groups = append(groups, g)
			}
		}
		newHook := object{"type": "command", "command": hookCommand(exe, false), "commandWindows": hookCommand(exe, true), "timeout": 65}
		group := object{"hooks": []any{newHook}}
		if event == "PreToolUse" {
			group["matcher"] = "^(Bash|exec_command)$"
		}
		groups = append(groups, group)
		hooks[event] = groups
	}
	changed := before != string(jsonBytes(config))
	if changed {
		if e = os.MkdirAll(home, 0700); e != nil {
			return nil, e
		}
		output, e := json.MarshalIndent(config, "", "  ")
		if e != nil {
			return nil, e
		}
		output = append(output, '\n')
		tmp, e := os.CreateTemp(home, ".xsl-hooks-*")
		if e != nil {
			return nil, e
		}
		defer os.Remove(tmp.Name())
		if e = tmp.Chmod(0600); e == nil {
			_, e = tmp.Write(output)
		}
		if ce := tmp.Close(); e == nil {
			e = ce
		}
		if e != nil {
			return nil, e
		}
		if e = os.Rename(tmp.Name(), p); e != nil {
			return nil, e
		}
	}
	return object{"changed": changed, "message": "请在 Codex /hooks 审阅并信任小算狸钩子，然后发送新消息。"}, nil
}
func contextKey(conversation, turn string) string { return "codex-turn-" + hash(conversation+"/"+turn) }
func readContext(c *client) (object, error) {
	key := os.Getenv("XSL_DIRECTOR_CONTEXT_KEY")
	if !regexp.MustCompile(`^codex-turn-[a-f0-9]{64}$`).MatchString(key) {
		return nil, errors.New("缺少 Codex 消息接线；请信任钩子并发送新消息")
	}
	ctx, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	id, turn := str(ctx["conversationId"]), str(ctx["userTurnId"])
	if !turnPattern.MatchString(id) || !turnPattern.MatchString(turn) || contextKey(id, turn) != key {
		return nil, errors.New("Codex 消息上下文无效")
	}
	return ctx, nil
}
func handleHook(input object) (object, error) {
	id := str(input["agent_id"])
	if id == "" {
		id = str(input["session_id"])
	}
	turn := str(input["turn_id"])
	if !turnPattern.MatchString(id) || !turnPattern.MatchString(turn) {
		return nil, errors.New("缺少有效的消息标识")
	}
	c, e := newClient()
	if e != nil {
		return nil, e
	}
	key := contextKey(id, turn)
	event := str(input["hook_event_name"])
	if event == "UserPromptSubmit" {
		if e = c.store.set(key, object{"conversationId": id, "userTurnId": turn}); e != nil {
			return nil, e
		}
		e = c.beginTurn(id, turn)
		if e != nil && code(e) != "CHAPTER_NOT_OPEN" && code(e) != "LOGIN_REQUIRED" {
			return object{"hookSpecificOutput": object{"hookEventName": event, "additionalContext": "小算狸消息同步暂不可用；业务调用前需要恢复连接。"}}, nil
		}
		return object{"hookSpecificOutput": object{"hookEventName": event, "additionalContext": "小算狸已记录本条用户消息；业务调用使用随包工具。"}}, nil
	}
	if event != "PreToolUse" {
		return object{}, nil
	}
	field := ""
	switch str(input["tool_name"]) {
	case "Bash":
		field = "command"
	case "exec_command":
		field = "cmd"
	}
	args := obj(input["tool_input"])
	command := str(args[field])
	if field == "" || !strings.Contains(strings.ReplaceAll(command, "\\", "/"), "xiaosuanli-director/bin/xsl-director-") {
		return object{}, nil
	}
	saved, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	if str(saved["conversationId"]) != id || str(saved["userTurnId"]) != turn {
		return object{"hookSpecificOutput": object{"hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": "请先信任小算狸消息钩子并发送新消息。"}}, nil
	}
	updated := clone(args)
	updated[field] = bindCommand(command, key, runtime.GOOS == "windows")
	return object{"hookSpecificOutput": object{"hookEventName": event, "permissionDecision": "allow", "updatedInput": updated}}, nil
}
func printJSON(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	return enc.Encode(v)
}
func hookMain() {
	var input object
	if e := json.NewDecoder(os.Stdin).Decode(&input); e != nil {
		fmt.Fprintln(os.Stderr, "小算狸消息接线暂不可用")
		os.Exit(2)
	}
	out, e := handleHook(input)
	if e != nil {
		fmt.Fprintln(os.Stderr, "小算狸消息接线暂不可用，请检查连接状态后重试")
		os.Exit(2)
	}
	_ = printJSON(out)
}
