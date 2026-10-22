{
  core,
  registry,
  registryInputs,
  system ? "aarch64-darwin",
}:
let
  moduleRecord = entry: {
    name = entry.name;
    input = entry.input;
    url = entry.url;
    checkout = entry.checkout;
    bootstrapStrategy = entry.bootstrap.strategy;
    ownsMachineLocalConfiguration = entry.bootstrap.strategy != "none";
  };
in
{
  schema = "composition-nd-public-promotion-v1";
  version = 2;
  composition = {
    name = "composition-nd";
    repositoryUrl = "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git";
  };
  inherit system;
  modules = builtins.map moduleRecord registry;
  releaseChannels = {
    policy = "explicit-head-refs-v1";
    refs = builtins.listToAttrs (builtins.map (entry: {
      name = entry.input;
      value = entry.releaseRef;
    }) registry);
  };
  packageSetIdentity = core.lib.packageSetIdentity;
  hosts = builtins.attrNames core.lib.hosts;
  checks = [
    "application-claims"
    "agent-namespace"
    "contracts"
    "lock-provenance"
    "registered-input-checks"
    "tests"
    "virtual-machines"
  ];
  qualification = {
    entryPoint = "tools/qualify-promotion";
    mode = "exact-candidate";
  };
  promotableInputs = {
    rule = "registered-direct-inputs-only";
    names = registryInputs;
  };
}
