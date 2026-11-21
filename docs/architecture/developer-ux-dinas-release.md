# Developer UX public preview binding

The composition's Developer UX input points to the public
[developer-ux_dinas repository](https://github.com/Knowledge-Forge-AI/developer-ux_dinas)
over HTTPS. The registry uses input and checkout name `developer-ux_dinas`,
logical owner `developer-ux`, release ref `refs/heads/deploy`, and operator
control file `config/developer-ux.toml`.

The v0.0.2 preview locks the exact Developer UX v0.0.1 source. This adds the
adapter's Nixvim input and transitive flake dependencies. Nixpkgs and
nix-darwin still follow Core, so the adapter uses the composition's package set.
The existing overlay and `darwinModules.dinasIntegration` seams select the
development stack. Other adapter and Core pins remain as in v0.0.1.

The complete graph still requires authorized access to the unpublished
adapters. Source qualification and host activation remain separate operations.
