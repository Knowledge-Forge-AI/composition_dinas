{ pkgs, core, leaf, configurations, mkDarwinConfiguration, configAllowlist, promotion }:
let
  lib = pkgs.lib;
  hostName = builtins.head (builtins.attrNames configurations);
  dormant = configurations.${hostName};
  enabled = mkDarwinConfiguration {
    inherit hostName;
    extraModules = [ { virtual-machines.configuration = {
      enable = lib.mkForce true;
      tooling = lib.mkForce false;
      base.cpus = lib.mkForce 3;
    }; } ];
  };
  bundled = mkDarwinConfiguration {
    inherit hostName;
    extraModules = [ { virtual-machines.configuration.enable = lib.mkForce true; } ];
  };
  bare = core.lib.mkDarwinConfiguration { inherit hostName; };
  withDormantLeaf = core.lib.mkDarwinConfiguration {
    inherit hostName;
    extraModules = [ leaf.darwinModules.dinasIntegration ];
  };
  surfaces = host: {
    packages = map (p: p.outPath) host.config.environment.systemPackages;
    activation = host.config.system.activationScripts;
    launchd = host.config.launchd;
    casks = host.config.homebrew.casks;
  };
  commands = builtins.filter (p: (p.pname or p.name or "") == "dinas-vm")
    enabled.config.environment.systemPackages;
  command = builtins.head commands;
  permitsVagrant = (builtins.tryEval pkgs.vagrant.drvPath).success;
  bundledResult = builtins.tryEval (builtins.deepSeq
    (map (p: p.drvPath) bundled.config.environment.systemPackages) true);
in
assert leaf.packageSetIdentity == core.lib.packageSetIdentity;
assert leaf.inputs.linux.outPath != leaf.inputs.nixpkgs.outPath;
# Dormant source-foundation phase requirement; revisit with a Fusion-claim promotion.
assert leaf.applicationClaims == [ ];
assert builtins.readFile ../config/default/virtual-machines.toml
  == builtins.readFile (leaf + "/config/default/virtual-machines.toml");
assert configAllowlist."virtual-machines.toml" == [ "virtual-machines" ];
assert promotion.releaseChannels.refs.virtual-machines_dinas == "refs/heads/main";
assert surfaces bare == surfaces withDormantLeaf;
assert builtins.all (host:
  !host.config.virtual-machines.configuration.enable
  && host.config.virtual-machines.configuration.tooling
  && host.config.virtual-machines.configuration.root == "~/virtual-machines"
  && host.config.virtual-machines.configuration.base == { cpus = 2; memory = 2048; }
  && host.config.virtual-machines.configuration.kde == { cpus = 4; memory = 6144; }
  && !(builtins.any (p: (p.pname or p.name or "") == "dinas-vm")
    host.config.environment.systemPackages)
) (builtins.attrValues configurations);
assert builtins.length commands == 1;
assert enabled.config.virtual-machines.configuration.base.cpus == 3;
assert enabled.config.launchd == dormant.config.launchd;
assert enabled.config.homebrew.casks == dormant.config.homebrew.casks;
assert builtins.filter (p: p != command.outPath) (surfaces enabled).packages
  == (surfaces dormant).packages;
# tryEval exposes success, not error text. The native projection test below owns
# the specific prerequisite diagnostic: tests/test_virtual_machines.py.
assert bundledResult.success == permitsVagrant;
pkgs.runCommandLocal "composition-virtual-machines" { } ''
  ${command}/bin/dinas-vm --help > "$TMPDIR/help"
  grep -F 'project' "$TMPDIR/help"
  test -f ${command}/share/dinas-vm/libexec/vm.py
  if grep -F 'export DINAS_VAGRANT=' ${command}/bin/dinas-vm; then
    echo 'tooling=false unexpectedly supplied bundled Vagrant' >&2
    exit 1
  fi
  grep -F '"cpus":3' ${command}/bin/dinas-vm
  touch "$out"
''
