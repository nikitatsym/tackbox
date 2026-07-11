package main

import (
	"context"
	"fmt"
)

func sentryErr(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {}

func cases(ctx context.Context, err error) {
	authToken := "x"
	apiKey := "y"
	sessionToken := "z"
	var cfg struct{ Secret string }
	name := "n"

	// TP1: bare secret-named identifier as message
	sentryErr(ctx, authToken, err, nil, "auth.fail")
	// TP2: secret-named identifier interpolated into message
	sentryErr(ctx, fmt.Sprintf("refresh failed for %s", apiKey), err, nil, "auth.refresh")
	// TP3: secret-named identifier inside tags map
	sentryErr(ctx, "auth failed", err, map[string]string{"tok": sessionToken}, "auth.fail")
	// TP4: secret-named selector as dedupKey
	sentryErr(ctx, "x", err, nil, cfg.Secret)
	// TP5: concatenation carrying secret-named identifier
	sentryErr(ctx, "bad credential: "+authToken, err, nil, "auth.concat")

	// FP1: domain prose in message (gmux install site)
	sentryErr(ctx, "install: revoke after stash failed -- live token leak", err, map[string]string{"name": name}, "install.revoke_after_fail")
	// FP2: domain nouns in tags values and dedupKey (gmux tokens site)
	sentryErr(ctx, "agent_tokens: persist on create", err, map[string]string{"area": "tokens.persist", "store": "userkey"}, "tokens.persist:agent.create")
	// FP3: domain noun only in dedupKey literal
	sentryErr(ctx, "persist on flush", err, map[string]string{"store": "agent"}, "tokens.persist:agent.flush")
}
