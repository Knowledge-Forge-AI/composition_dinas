{ lib }:
let
  selection = import ../lib/browser-ux-host-selection.nix { inherit lib; rolesR3Selected = true; };
  inactive = import ../lib/browser-ux-host-selection.nix { inherit lib; };
  retained.browser-ux.configuration = {
    replacements = { vivaldi.enable = true; floorp.enable = true; };
    primaryCutover = { enable = true; activeBrowsers = ["vivaldi" "floorp" "safari"]; retiredBrowsers = ["chrome" "firefox"]; };
  };
  core = map (id: { owner = "core"; claim = { inherit id; }; })
    ["bundle:com.google.Chrome" "bundle:org.mozilla.firefox" "bundle:com.brave.Browser" "bundle:com.kagi.kagimacOS"];
in
assert inactive.casksForHost "example-primary" retained == [ "vivaldi" "floorp" ];
assert inactive.casksForHost "example-workstation" retained == [];
assert selection.casksForHost "example-primary" retained == [];
assert selection.casksForHost "example-workstation" retained == [];
assert selection.activeClaimsForHost "example-primary" retained core == core;
assert selection.activeClaimsForHost "example-workstation" retained core == core;
assert !(selection.moduleForHost "example-workstation" retained).browser-ux.configuration ? primaryCutover;
true
