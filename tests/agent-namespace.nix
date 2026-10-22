{ pkgs, agentInput, configurations }:

let
  hosts = builtins.attrValues configurations;
  # Public configurations use the input's default native aggregate. Compare
  # against its public package factory, without copying module override internals.
  # This proves consumption of the locked input, not release freshness; selection
  # of an approved revision is a separate lock-maintenance qualification gate.
  expectedPackage = host: (agentInput.lib.mkPackages host.pkgs).agent-clis;
  matchesInput = host: package:
    package.drvPath == (expectedPackage host).drvPath;
  selectedPackages = builtins.map
    (host: host.config.dinas.commandNamespaces.agent.package)
    hosts;
in
assert hosts != [ ];
assert builtins.all (host:
  matchesInput host host.config.dinas.commandNamespaces.agent.package
  && !(matchesInput host host.pkgs.hello)
  && host.config.programs.agent-clis.mode == "native"
  && !host.config.programs.agent-clis.enable
  && host.config.dinas.commandNamespaces.agent.binSubdir == "bin") hosts;
pkgs.runCommandLocal "composition-dinas-agent-namespace"
  { packages = selectedPackages; }
  ''
    for package in $packages; do
      for command in codex claude agy agent-cli-update; do
        test -x "$package/bin/$command"
      done
    done
    touch "$out"
  ''
