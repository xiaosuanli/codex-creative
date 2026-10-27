package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "请指定命令")
		os.Exit(1)
	}
	command := os.Args[1]
	if command == "hook" {
		hookMain()
		return
	}
	if command == "setup" {
		out, e := setupHooks()
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
		_ = printJSON(out)
		return
	}
	out, e := run(command)
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
	_ = printJSON(out)
}
func run(command string) (object, error) {
	c, e := newClient()
	if e != nil {
		return nil, e
	}
	input := object{}
	switch command {
	case "auth-complete", "chapters", "open-chapter", "create-project", "create-chapter", "execute":
		b, e := io.ReadAll(io.LimitReader(os.Stdin, 2*1024*1024+1))
		if e != nil {
			return nil, e
		}
		if len(b) > 2*1024*1024 {
			return nil, errors.New("输入过长，请选择单章范围")
		}
		if json.Unmarshal(b, &input) != nil || input == nil {
			return nil, errors.New("输入必须为 JSON 对象")
		}
	}
	ctx := object{}
	switch command {
	case "context", "open-chapter", "create-project", "create-chapter", "execute":
		ctx, e = readContext(c)
		if e != nil {
			return nil, e
		}
	}
	id, turn := str(ctx["conversationId"]), str(ctx["userTurnId"])
	switch command {
	case "auth-start":
		return c.authStart()
	case "auth-complete":
		return c.authComplete(input)
	case "status":
		return c.status()
	case "account":
		g, e := c.grant()
		if e != nil {
			return nil, e
		}
		return c.request("/api/subscriber/account", g, nil, "GET", "")
	case "projects":
		return c.projects()
	case "chapters":
		return c.chapters(str(input["projectId"]))
	case "capabilities":
		return c.capabilities()
	case "context":
		return c.context(id)
	case "open-chapter":
		if _, e = c.open(id, str(input["chapterId"]), str(input["projectId"])); e != nil {
			return nil, e
		}
		if e = c.beginTurn(id, turn); e != nil {
			return nil, e
		}
		return c.context(id)
	case "create-project":
		return c.create("project", input, id)
	case "create-chapter":
		return c.create("chapter", input, id)
	case "execute":
		for k := range input {
			if k != "operation" && k != "arguments" {
				return nil, errors.New("业务请求不能覆盖宿主身份")
			}
		}
		if e = c.beginTurn(id, turn); e != nil {
			return nil, e
		}
		return c.execute(str(input["operation"]), obj(input["arguments"]), id, turn)
	case "logout":
		return c.logout()
	default:
		return nil, errors.New("不支持的命令")
	}
}
