{
  description = "Public-capable nix-darwin composition";

  inputs = {
    core.url = "git+ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git";
    agentic-praxis-grimoire_dinas.url =
      "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?ref=main";
    agentic-praxis-grimoire_dinas.inputs.nixpkgs.follows = "core/nixpkgs";
    agentic-praxis-grimoire_dinas.inputs.nix-darwin.follows = "core/nix-darwin";
    entertainment-nd.url =
      "git+ssh://git@github.com/lair001/entertainment-nd.git?ref=main";
    entertainment-nd.inputs.nixpkgs.follows = "core/nixpkgs";
    entertainment-nd.inputs.nix-darwin.follows = "core/nix-darwin";
    developer-ux_dinas.url =
      "git+ssh://git@github.com/lair001/developer-ux_dinas.git?ref=deploy";
    developer-ux_dinas.inputs.nixpkgs.follows = "core/nixpkgs";
    developer-ux_dinas.inputs.nix-darwin.follows = "core/nix-darwin";
    browser-ux_dinas.url =
      "git+ssh://git@github.com/Knowledge-Forge-AI/browser-ux_dinas.git?ref=main";
    browser-ux_dinas.inputs.nixpkgs.follows = "core/nixpkgs";
    browser-ux_dinas.inputs.nix-darwin.follows = "core/nix-darwin";
    agent_dinas.url =
      "git+ssh://git@github.com/lair001/agent_dinas.git?ref=main";
    agent_dinas.inputs.nixpkgs.follows = "core/nixpkgs";
    agent_dinas.inputs.nix-darwin.follows = "core/nix-darwin";
    virtual-machines_dinas.url =
      "git+ssh://git@github.com/lair001/virtual-machines_dinas.git?ref=main";
    virtual-machines_dinas.inputs.nixpkgs.follows = "core/nixpkgs";
    virtual-machines_dinas.inputs.nix-darwin.follows = "core/nix-darwin";
    localConfig.url = "path:./config/default";
  };

  outputs = inputs@{ self, core, localConfig, ... }:
    let
      unique = values: builtins.foldl'
        (result: value: if builtins.elem value result then result else result ++ [ value ])
        [ ]
        values;
      mapAttrs = function: attrs: builtins.listToAttrs (
        builtins.map
          (name: { inherit name; value = function name attrs.${name}; })
          (builtins.attrNames attrs)
      );
      system = "aarch64-darwin";
      pkgs = core.legacyPackages.${system};

      normalizeUrl = url:
        let
          noGitPlus =
            if pkgs.lib.hasPrefix "git+ssh://" url then
              pkgs.lib.removePrefix "git+" url
            else if pkgs.lib.hasPrefix "git+https://" url then
              pkgs.lib.removePrefix "git+" url
            else url;
          noGitSuffix =
            if pkgs.lib.hasSuffix ".git" noGitPlus then
              builtins.substring 0 (builtins.stringLength noGitPlus - 4) noGitPlus
            else noGitPlus;
        in
        noGitSuffix;

      validReleaseRef = ref:
        let
          prefix = "refs/heads/";
          short = pkgs.lib.removePrefix prefix ref;
          components = pkgs.lib.splitString "/" short;
          componentValid = component:
            component != ""
            && component != "HEAD"
            && builtins.match "[A-Za-z0-9][A-Za-z0-9._-]*" component != null
            && !pkgs.lib.hasPrefix "." component
            && !pkgs.lib.hasSuffix "." component
            && !pkgs.lib.hasSuffix ".lock" component;
        in
        pkgs.lib.hasPrefix prefix ref
        && short != "@"
        && !pkgs.lib.hasPrefix "refs/" short
        && !pkgs.lib.hasInfix ".." short
        && !pkgs.lib.hasInfix "@{" short
        && !pkgs.lib.hasInfix "//" short
        && builtins.all componentValid components;
      normalizeReleaseRef = ref:
        let full = if pkgs.lib.hasPrefix "refs/heads/" ref then ref else "refs/heads/${ref}";
        in if validReleaseRef full then full else "";

      registryDocument = builtins.fromJSON (builtins.readFile ./registry.json);
      registry = registryDocument.modules or [ ];
      registryDocumentValid = builtins.attrNames registryDocument == [ "modules" "schema" ]
        && registryDocument.schema == "nix-darwin-composition-registry-v2";
      registryEntryValid = entry:
        builtins.attrNames entry == [ "bootstrap" "checkout" "input" "name" "releaseRef" "url" ]
        && builtins.match "[A-Za-z0-9][A-Za-z0-9._-]*" entry.name != null
        && validReleaseRef entry.releaseRef
        && builtins.attrNames entry.bootstrap ==
          (if entry.bootstrap.strategy == "repo-tool"
           then [ "config" "executable" "strategy" ]
           else if entry.bootstrap.strategy == "copy-default"
           then [ "config" "source" "strategy" ]
           else if entry.bootstrap.strategy == "none"
           then [ "strategy" ]
           else [ ])
        && (entry.bootstrap.strategy == "none"
          || entry.bootstrap.config == "config/${entry.name}.toml")
        && (entry.bootstrap.strategy != "copy-default"
          || entry.bootstrap.source == "config/default/${entry.name}.toml");
      registryEntriesValid = builtins.all registryEntryValid registry;
      registryNames = builtins.map (entry: entry.name) registry;
      registryUnique = builtins.length registryNames == builtins.length (unique registryNames);
      registryInputs = builtins.map (entry: entry.input) registry;
      registryInputsUnique = builtins.length registryInputs
        == builtins.length (unique registryInputs);
      compositionInputNames = builtins.filter
        (name: !(builtins.elem name [ "core" "localConfig" "self" ]))
        (builtins.attrNames inputs);
      registryInputsComplete = builtins.length compositionInputNames
        == builtins.length registryInputs
        && builtins.all (name: builtins.elem name registryInputs) compositionInputNames;
      registryCheckouts = builtins.map (entry: entry.checkout) registry;
      registryCheckoutsUnique = builtins.length registryCheckouts
        == builtins.length (unique registryCheckouts);
      registryUrls = builtins.map (entry: entry.url) registry;
      registryUrlsUnique = builtins.length registryUrls
        == builtins.length (unique registryUrls);
      configuredRegistry = builtins.filter
        (entry: entry.bootstrap.strategy != "none")
        registry;
      registeredInputs = builtins.map (entry: inputs.${entry.input}) registry;
      registeredModules = builtins.map
        (input: input.darwinModules.dinasIntegration or input.darwinModules.default)
        registeredInputs;
      registeredOverlays = builtins.concatMap
        (input: if input ? overlays.default then [ input.overlays.default ] else [ ])
        registeredInputs;

      lockDocument = builtins.fromJSON (builtins.readFile ./flake.lock);
      lockRootInputs = lockDocument.nodes.${lockDocument.root}.inputs or { };
      lockNodeFor = inputName: lockDocument.nodes.${lockRootInputs.${inputName}} or { };
      lockEntryValidFor = entry: node:
            node ? inputs
            && node.inputs.nixpkgs or null == [ "core" "nixpkgs" ]
            && node.inputs.nix-darwin or null == [ "core" "nix-darwin" ]
            && (normalizeUrl entry.url == normalizeUrl (node.locked.url or ""))
            && normalizeReleaseRef (node.original.ref or "") == entry.releaseRef
            && normalizeReleaseRef (node.locked.ref or "") == entry.releaseRef;
      lockEntryValid = entry:
        builtins.hasAttr entry.input lockRootInputs
        && lockEntryValidFor entry (lockNodeFor entry.input);
      lockEntriesValid = builtins.all lockEntryValid registry;
      lockRefMismatchRejected = builtins.all
        (entry:
          let
            node = lockNodeFor entry.input;
            mismatchedShort =
              if entry.releaseRef == "refs/heads/main" then "deploy" else "main";
            mismatchedNode = node // {
              original = (node.original or { }) // { ref = mismatchedShort; };
              locked = (node.locked or { }) // { ref = mismatchedShort; };
            };
          in
          !lockEntryValidFor entry mismatchedNode)
        registry;
      noRegisteredInputsAreDarwinConfigurations = builtins.all
        (input: !(input ? darwinConfigurations))
        registeredInputs;

      claimsLib = core.lib.applicationClaims;
      claimsForInputOnHost = hostName: name: input:
        if !(input ? applicationClaims) then
          builtins.throw "composition-nd: ${name} must declare applicationClaims"
        else
          let
            rawClaims =
              if name == "browser-ux" && (core.lib.browserRolesR3Selected or false) && input ? applicationClaimsForHost then
                input.applicationClaimsForHost { inherit hostName; }
              else if name == "browser-ux" && input ? applicationClaimsFor then
                input.applicationClaimsFor (moduleConfiguration.browser-ux.configuration.replacements or { })
              else input.applicationClaims;
          in claimsLib.attachOwner name rawClaims;
      claimsForInput = claimsForInputOnHost "example-primary";
      coreHostApplicationClaims = hostName:
        if core ? applicationClaimsFor
        then core.applicationClaimsFor { inherit hostName system; }
        else core.applicationClaims;
      applicationClaimsAggregate = claimsLib.attachOwner "core" core.applicationClaims
        ++ builtins.concatMap
          (entry: claimsForInput entry.name inputs.${entry.input})
          registry;
      validatedApplicationClaims =
        claimsLib.assertValid applicationClaimsAggregate applicationClaimsAggregate;
      applicationClaimsGate = builtins.deepSeq validatedApplicationClaims true;

      projectionAllowlistFor = entry:
        let
          filename = builtins.baseNameOf entry.bootstrap.config;
          declared = inputs.${entry.input}.configAllowlist or { };
          declaredKeys = declared.${filename} or [ ];
          prefix = "${entry.name}.";
          hasModulePrefix = key:
            builtins.substring 0 (builtins.stringLength prefix) key == prefix;
        in
        if builtins.attrNames declared != [ filename ] then
          builtins.throw "composition-nd: ${entry.name} must own exactly ${filename}"
        else if declaredKeys == [ ] then
          builtins.throw "composition-nd: ${entry.name} declares no configuration keys"
        else if !builtins.all hasModulePrefix declaredKeys then
          builtins.throw "composition-nd: ${entry.name} declares a key outside its table"
        else
          { ${filename} = [ entry.name ]; };
      allowlists = [ core.configAllowlist ]
        ++ builtins.map projectionAllowlistFor configuredRegistry;
      allowlistKeys = builtins.concatMap builtins.attrNames allowlists;
      allowlistsUnique = builtins.length allowlistKeys
        == builtins.length (unique allowlistKeys);
      aggregatedAllowlist = builtins.foldl' (current: next: current // next) { } allowlists;
      packageSetIdentities = [ core.lib.packageSetIdentity ]
        ++ builtins.map (input: input.packageSetIdentity) registeredInputs;
      packageSetEqual = builtins.all
        (identity: identity == core.lib.packageSetIdentity)
        packageSetIdentities;
      shellContributions = builtins.concatMap
        (input: input.shellContributions or [ ])
        registeredInputs;
      shellNames = builtins.map (contribution: contribution.name) shellContributions;
      shellExports = builtins.concatMap (contribution: contribution.exports or [ ]) shellContributions;
      shellNamesUnique = builtins.length shellNames
        == builtins.length (unique shellNames);
      shellExportsUnique = builtins.length shellExports
        == builtins.length (unique shellExports);

      configurationFor = entry: document:
        let filename = builtins.baseNameOf entry.bootstrap.config; in
        if builtins.attrNames document != [ entry.name ] then
          builtins.throw
            "composition-nd: ${filename} must contain exactly the [${entry.name}] table"
        else if !builtins.isAttrs document.${entry.name} then
          builtins.throw
            "composition-nd: [${entry.name}] in ${filename} must be a table"
        else
          document.${entry.name};
      configurationModuleFor = entry: {
        name = entry.name;
        value.configuration = configurationFor entry (
          builtins.fromTOML (
            builtins.readFile (
              localConfig.outPath + "/" + builtins.baseNameOf entry.bootstrap.config
            )
          )
        );
      };
      moduleConfiguration = builtins.listToAttrs (
        builtins.map configurationModuleFor configuredRegistry
      );

      firstConfiguredEntry = builtins.head configuredRegistry;
      firstConfiguredPartialDoc = builtins.listToAttrs [ {
        name = firstConfiguredEntry.name;
        value = { };
      } ];
      partialConfigurationAccepted = builtins.tryEval (
        builtins.deepSeq
          (configurationFor firstConfiguredEntry firstConfiguredPartialDoc)
          true
      );
      missingConfigurationRejected = builtins.tryEval (
        builtins.deepSeq (configurationFor firstConfiguredEntry { }) true
      );
      extraConfigurationRejected = builtins.tryEval (
        builtins.deepSeq
          (configurationFor firstConfiguredEntry (firstConfiguredPartialDoc // { extra = { }; }))
          true
      );
      configuredTableRejections = builtins.all
        (entry:
          let
            partialDoc = builtins.listToAttrs [ {
              name = entry.name;
              value = { };
            } ];
            partialAccepted = builtins.tryEval (
              builtins.deepSeq (configurationFor entry partialDoc) true
            );
            missingRejected = builtins.tryEval (
              builtins.deepSeq (configurationFor entry { }) true
            );
            extraRejected = builtins.tryEval (
              builtins.deepSeq (configurationFor entry (partialDoc // { extra = { }; })) true
            );
          in
          partialAccepted.success && !missingRejected.success && !extraRejected.success)
        configuredRegistry;
      allConfiguredInModuleConfiguration = builtins.all
        (entry: builtins.hasAttr entry.name moduleConfiguration)
        configuredRegistry;

      # Validate first: malformed IDs must not reach normalization below.
      claimsAccepted = entries:
        (claimsLib.validate entries).ok && authorizedOverlap entries;
      claimsRejected = entries: !(claimsAccepted entries);
      validationHasRule = rule: entries:
        let result = claimsLib.validate entries; in
        !result.ok
        && builtins.any
          (error: pkgs.lib.hasInfix "(${rule})" error)
          result.errors;
      syntheticClaim = id: package: artifact: {
        inherit artifact package;
        id = "bundle:org.example.${id}";
        provider = "homebrew-cask";
      };
      handoffAdr =
        "docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md";
      handoffPair = from:
        let
          claim = syntheticClaim "handoff" "synthetic-handoff" "Synthetic Handoff.app";
        in
        claimsLib.attachOwner "synthetic-source" [ claim ]
        ++ claimsLib.attachOwner "synthetic-destination" [
          (claim // { handoff = { inherit from; adr = handoffAdr; }; })
        ];
      missingClaimsRejected = builtins.tryEval (
        builtins.deepSeq (claimsForInput "synthetic-missing" { }) true
      );
      aggregateOwnerOrder = unique (
        builtins.map (entry: entry.owner) applicationClaimsAggregate
      );
      contributingRegistryNames = builtins.filter
        (name: builtins.any (entry: entry.owner == name) applicationClaimsAggregate)
        registryNames;

      authorizedOverlap = entries:
        let
          normalizedIds = builtins.map (entry: claimsLib.normalizeId entry.claim.id) entries;
          duplicateIds = builtins.filter
            (id: count id normalizedIds > 1) (unique normalizedIds);
          handoffIds = unique (builtins.map
            (entry: claimsLib.normalizeId entry.claim.id)
            (builtins.filter (entry: entry.claim ? handoff) entries));
          sorted = builtins.sort builtins.lessThan;
        in sorted duplicateIds == sorted handoffIds;

      validHandoff = handoffPair "synthetic-source";
      invalidHandoffFixtures = [
        { rule = "duplicate-claim"; entries = syntheticDuplicateClaims; }
        { rule = "singleton-handoff"; entries = [ (builtins.elemAt validHandoff 1) ]; }
        { rule = "handoff-source-mismatch"; entries = handoffPair "synthetic-unknown"; }
        {
          rule = "too-many-claims";
          entries = validHandoff ++ [ ((builtins.head validHandoff) // { owner = "synthetic-third"; }) ];
        }
        {
          rule = "mutual-handoff";
          entries = [
            { owner = "synthetic-source"; claim = (builtins.head validHandoff).claim // {
              handoff = { from = "synthetic-destination"; adr = handoffAdr; };
            }; }
            (builtins.elemAt validHandoff 1)
          ];
        }
      ] ++ builtins.map (key: {
        rule = "unknown-keys";
        entries = claimsLib.attachOwner "synthetic" [
          (syntheticDuplicateClaim // { ${key} = "accidental provenance"; })
        ];
      }) [ "surface" "declaration" "unexpected" ];

      syntheticDuplicateClaim =
        syntheticClaim "duplicate" "synthetic-duplicate" "Synthetic Duplicate.app";
      syntheticDuplicateClaims =
        claimsLib.attachOwner "synthetic-source" [ syntheticDuplicateClaim ]
        ++ claimsLib.attachOwner "synthetic-destination" [ syntheticDuplicateClaim ];

      contractValid = registryDocumentValid
        && registryEntriesValid
        && registryUnique
        && registryInputsUnique
        && registryInputsComplete
        && registryCheckoutsUnique
        && registryUrlsUnique
        && allowlistsUnique
        && packageSetEqual
        && shellNamesUnique
        && shellExportsUnique
        && lockEntriesValid
        && lockRefMismatchRejected
        && noRegisteredInputsAreDarwinConfigurations;

      browserHostSelection = import ./lib/browser-ux-host-selection.nix {
        lib = pkgs.lib;
        rolesR3Selected = core.lib.browserRolesR3Selected or false;
      };

      applicationClaimsForHost = hostName:
        let
          coreClaims = coreHostApplicationClaims hostName;
          registryClaims = builtins.concatMap
            (entry:
              let attached = claimsForInputOnHost hostName entry.name inputs.${entry.input};
              in builtins.filter (browserHostSelection.claimAppliesToHost hostName) attached
            )
            registry;
          aggregate = (claimsLib.attachOwner "core" coreClaims) ++ registryClaims;
        in assert contractValid;
          browserHostSelection.activeClaimsForHost hostName moduleConfiguration aggregate;

      mkDarwinConfigurationWith = compositionModules: compositionOverlays:
        {
          hostName,
          hostConfig ? core.lib.hosts.${hostName},
          extraModules ? [ ],
          extraOverlays ? [ ],
        }:
        assert contractValid;
        assert applicationClaimsGate;
        assert !(core.lib.browserRolesR3Selected or false) || hostName != "example-primary" || inputs.browser-ux_dinas ? browserRolesR3;
        core.lib.mkDarwinConfiguration {
          inherit hostName hostConfig;
          extraModules = compositionModules
            ++ [ (browserHostSelection.moduleForHost hostName moduleConfiguration) ]
            ++ pkgs.lib.optionals (browserHostSelection.casksForHost hostName moduleConfiguration != [ ]) [
              { homebrew.casks = browserHostSelection.casksForHost hostName moduleConfiguration; }
            ] ++ extraModules;
          extraOverlays = compositionOverlays ++ extraOverlays;
        };
      mkDarwinConfiguration = mkDarwinConfigurationWith
        registeredModules
        registeredOverlays;

      publicHostNames = builtins.attrNames core.lib.hosts;
      firstHost = builtins.head publicHostNames;
      compositionCallerOverlay = _final: previous: {
        compositionCallerSeesCore = builtins.hasAttr "rtk" previous;
      };
      compositionProbe = mkDarwinConfiguration {
        hostName = firstHost;
        extraOverlays = [ compositionCallerOverlay ];
      };

      count = value: values: builtins.length
        (builtins.filter (candidate: candidate == value) values);

      composedCaskNames = builtins.map
        (cask: cask.name)
        compositionProbe.config.homebrew.casks;
      moduleClaims = builtins.filter
        (entry: entry.owner != "core")
        applicationClaimsAggregate;
      # This checks composed identity, not writer ownership. An activation mention
      # is evidence accepted by this contract, not proof of bundle generation.
      claimIsRealized = surfaces: entry:
        let
          claim = entry.claim;
          activationRealized = pkgs.lib.hasPrefix "bundle:" claim.id
            && builtins.elem (pkgs.lib.removePrefix "bundle:" claim.id)
              (builtins.filter builtins.isString
                (builtins.split "[^A-Za-z0-9.-]+" surfaces.activationText));
          caskCount = count claim.package surfaces.caskNames;
        in
        if claim.provider == "homebrew-cask" then
          caskCount == 1 || (caskCount == 0 && activationRealized)
        else if claim.provider == "nix" then
          builtins.elem claim.package surfaces.packageNames || activationRealized
        else
          true;
      realizationSurfaces = configuration: {
        caskNames = builtins.map (cask: cask.name) configuration.config.homebrew.casks;
        packageNames = builtins.map (package: package.pname or package.name or "")
          configuration.config.environment.systemPackages;
        activationText = configuration.config.system.activationScripts.postActivation.text or "";
      };
      realizedClaimsValid = builtins.all
        (host: builtins.all (claimIsRealized (realizationSurfaces self.darwinConfigurations.${host}))
          (builtins.filter (browserHostSelection.claimAppliesToHost host) moduleClaims))
        publicHostNames;
      realizationTestsValid =
        let
          claim = (syntheticClaim "MixedCase" "synthetic-app" "Synthetic.app") // { provider = "nix"; };
          empty = { caskNames = [ ]; packageNames = [ ]; activationText = ""; };
          realized = surfaces: value: claimIsRealized surfaces { owner = "synthetic"; claim = value; };
          activation = empty // { activationText = "bundle_id='org.example.MixedCase'\n"; };
          cask = claim // { provider = "homebrew-cask"; };
        in
        realized (empty // { packageNames = [ "synthetic-app" ]; }) claim
        && realized activation claim
        && !(realized empty claim)
        && !(realized (empty // { activationText = "org.example.MixedCase.helper"; }) claim)
        && !(realized (empty // { activationText = "prefix.org.example.MixedCase"; }) claim)
        && !(realized (empty // { activationText = "org.example.mixedcase"; }) claim)
        && !(realized activation (claim // { id = "command:org.example.MixedCase"; }))
        && realized (empty // { caskNames = [ "synthetic-app" ]; }) cask
        && realized activation cask
        && !(realized empty cask)
        && !(realized (activation // { caskNames = [ "synthetic-app" "synthetic-app" ]; }) cask);

      forbiddenCasks = [ "codex-app" ];
      forbiddenCasksAbsent = builtins.all
        (name: !(builtins.elem name composedCaskNames))
        forbiddenCasks;

      promotionContract = import ./lib/promotion.nix {
        inherit core registry registryInputs system;
      };
      expectedReleaseChannels = {
        policy = "explicit-head-refs-v1";
        refs = builtins.listToAttrs (builtins.map (entry: {
          name = entry.input;
          value = entry.releaseRef;
        }) registry);
      };

      registeredInputCheckDerivations = builtins.concatMap
        (input:
          if input ? checks.${system} then
            builtins.attrValues input.checks.${system}
          else [ ])
        registeredInputs;
    in
    {
      lib = assert contractValid; {
        inherit mkDarwinConfiguration;
        hosts = core.lib.hosts;
        packageSetIdentity = core.lib.packageSetIdentity;
        registeredModuleNames = registryNames;
        applicationClaims = claimsLib;
        promotion = promotionContract;
        storageOfficeOwnershipVersion = core.lib.storageOfficeOwnershipVersion or 0;
      } // (pkgs.lib.optionalAttrs (core.lib ? userHomeWriterCommon) {
        inherit (core.lib) userHomeWriterCommon;
      });

      applicationClaims = assert contractValid; validatedApplicationClaims;
      applicationClaimsFor = applicationClaimsForHost;
      applicationClaimsForHost = applicationClaimsForHost;
      packageSetIdentity = assert contractValid; core.lib.packageSetIdentity;
      configAllowlist = assert contractValid; aggregatedAllowlist;
      shellContributions = assert contractValid; shellContributions;

      darwinConfigurations = assert contractValid; mapAttrs
        (hostName: hostConfig: mkDarwinConfiguration {
          inherit hostName hostConfig;
        })
        core.lib.hosts;

      checks.${system} = {
        virtual-machines = import ./tests/virtual-machines.nix {
          inherit pkgs core mkDarwinConfiguration;
          leaf = inputs.virtual-machines_dinas;
          configurations = self.darwinConfigurations;
          configAllowlist = aggregatedAllowlist;
          promotion = promotionContract;
        };

        agent-namespace = import ./tests/agent-namespace.nix {
          inherit pkgs;
          agentInput = inputs.agent_dinas;
          configurations = self.darwinConfigurations;
        };

        contracts =
          assert import ./tests/browser-role-r3.nix { lib = pkgs.lib; };
          assert contractValid;
          assert builtins.deepSeq moduleConfiguration true;
          assert compositionProbe.pkgs.compositionCallerSeesCore;
          assert realizedClaimsValid;
          assert realizationTestsValid;
          assert forbiddenCasksAbsent;
          assert partialConfigurationAccepted.success;
          assert !missingConfigurationRejected.success;
          assert !extraConfigurationRejected.success;
          assert configuredTableRejections;
          assert allConfiguredInModuleConfiguration;
          assert promotionContract.schema == "composition-nd-public-promotion-v1";
          assert promotionContract.version == 2;
          assert promotionContract.releaseChannels == expectedReleaseChannels;
          assert promotionContract.modules == builtins.map (entry: {
            inherit (entry) name input url checkout;
            bootstrapStrategy = entry.bootstrap.strategy;
            ownsMachineLocalConfiguration = entry.bootstrap.strategy != "none";
          }) registry;
          assert promotionContract.hosts == builtins.attrNames core.lib.hosts;
          assert promotionContract.packageSetIdentity == core.lib.packageSetIdentity;
          assert promotionContract.promotableInputs.names == registryInputs;
          pkgs.runCommandLocal "composition-nd-contracts" { } ''
            touch "$out"
          '';

        application-claims =
          assert applicationClaimsGate;
          assert builtins.deepSeq self.applicationClaims true;
          assert builtins.all
            (entry: entry.owner == "core")
            (claimsLib.attachOwner "core" core.applicationClaims);
          assert aggregateOwnerOrder == [ "core" ] ++ contributingRegistryNames;
          assert claimsAccepted applicationClaimsAggregate;
          assert claimsAccepted (claimsLib.attachOwner "synthetic" [ syntheticDuplicateClaim ]);
          assert claimsAccepted validHandoff;
          assert builtins.all (fixture:
            claimsRejected fixture.entries && validationHasRule fixture.rule fixture.entries)
            invalidHandoffFixtures;
          assert validationHasRule "duplicate-claim" syntheticDuplicateClaims;
          assert validationHasRule
            "handoff-source-mismatch"
            (handoffPair "synthetic-unknown");
          assert validationHasRule "singleton-handoff" (
            claimsLib.attachOwner "synthetic-destination" [
              ((syntheticClaim "lone" "synthetic-lone" "Synthetic Lone.app") // {
                handoff = { from = "synthetic-source"; adr = handoffAdr; };
              })
            ]
          );
          assert !missingClaimsRejected.success;
          assert claimsRejected (claimsLib.attachOwner "synthetic" [
            ((syntheticClaim "override" "synthetic" "Synthetic Override.app") // {
              owner = "core";
            })
          ]);
          assert claimsAccepted applicationClaimsAggregate;
          assert claimsRejected (
            applicationClaimsAggregate
            ++ claimsLib.attachOwner "synthetic" inputs.developer-ux_dinas.lib.editorComponents.vscode.applicationClaims
          );
          assert claimsRejected (claimsLib.attachOwner "synthetic" [
            (syntheticClaim "collide-one" "synthetic-one" "Synthetic Collision.app")
            (syntheticClaim "collide-two" "synthetic-two" "Synthetic Collision.app")
          ]);
          assert claimsRejected (claimsLib.attachOwner "synthetic" [
            (syntheticClaim "drift-one" "synthetic-drift" "Synthetic Drift One.app")
            (syntheticClaim "drift-two" "synthetic-drift" "Synthetic Drift Two.app")
          ]);
          assert claimsAccepted (handoffPair "synthetic-source");
          assert claimsRejected (handoffPair "synthetic-unknown");
          assert claimsRejected (claimsLib.attachOwner "synthetic" [
            ((syntheticClaim "lone" "synthetic-lone" "Synthetic Lone.app") // {
              handoff = { from = "synthetic-source"; adr = handoffAdr; };
            })
          ]);
          assert claimsRejected (claimsLib.attachOwner "synthetic" [
            ((syntheticClaim "stale" "synthetic-stale" "Synthetic Stale.app") // {
              suite = true;
            })
          ]);
          assert claimsAccepted (self.applicationClaimsFor "example-primary");
          assert claimsAccepted (self.applicationClaimsFor "example-workstation");
          assert self.applicationClaimsFor "example-primary" ==
            browserHostSelection.activeClaimsForHost "example-primary" moduleConfiguration self.applicationClaims;
          assert claimsAccepted (
            self.applicationClaims
            ++ claimsLib.attachOwner "synthetic-private" [
              (syntheticClaim "private" "synthetic-private" "Synthetic Private.app")
            ]
          );
          pkgs.runCommandLocal "composition-nd-application-claims" { } ''
            touch "$out"
          '';

        registered-input-checks = pkgs.runCommandLocal "composition-nd-registered-input-checks"
          {
            checks = registeredInputCheckDerivations;
          }
          ''
            touch "$out"
          '';

        tests = pkgs.runCommandLocal "composition-nd-tests"
          {
            nativeBuildInputs = [ pkgs.python3 pkgs.git ];
            src = self;
          }
          ''
            export HOME="$TMPDIR/home"
            export PYTHONDONTWRITEBYTECODE=1
            mkdir -p "$HOME"
            cp -R "$src" source
            chmod -R u+w source
            source/tests/run
            touch "$out"
          '';

        lock-provenance = pkgs.runCommandLocal "composition-nd-lock-provenance"
          {
            nativeBuildInputs = [ pkgs.python3 pkgs.git ];
            src = self;
          }
          ''
            export PYTHONDONTWRITEBYTECODE=1
            cp -R "$src" source
            chmod -R u+w source
            source/tools/check-inherited-lock-provenance \
              --composition-lock source/flake.lock \
              --core-lock ${core}/flake.lock \
              > "$out"
          '';
      };
    };
}
