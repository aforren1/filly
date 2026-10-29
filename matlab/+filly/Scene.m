classdef Scene < filly.Object
    % SCENE  Models, lights, a camera, an environment, and output settings.
    %   Create one with renderer.createScene(). See the Python API reference for the meaning
    %   of each property; names are the same in lowerCamelCase.

    properties (Dependent)
        camera
        encoding
        outputPath
        toneMapping
        antialiasing
        msaa
        shadows
        transparent
        dithering
        ssao
        bloom
        fog
        fogColor
        fogDensity
        fogStart
        depthOfField
        vignette
        vignetteMidpoint
        vignetteRoundness
        vignetteFeather
        vignetteColor
        environment
        environmentIntensity
        environmentRotationDeg
        environmentVisible
        background
        pickBlended
    end
    properties (Dependent, SetAccess = private)
        closed
    end

    methods
        function obj = Scene(owner, id)
            obj@filly.Object(owner, id);
        end

        function close(obj)
            filly_mex('Scene.close', obj.Id);
        end

        function model = load(obj, source, varargin)
            % Load a glTF or GLB file (char path) or GLB bytes (uint8) into the scene.
            % Option 'Strict' (false), as for Renderer.loadAsset.
            o = filly.internal.options(struct('Strict', false), varargin);
            if ~isa(source, 'uint8'), source = filly.internal.text(source); end
            [id, warnings] = filly_mex('Scene.load', obj.Id, source, o.Strict);
            model = filly.Model(obj.Owner, id);
            filly.Renderer.warn(warnings);
        end

        function model = add(obj, asset)
            % A new instance of an asset from renderer.loadAsset() or renderer.createMeshAsset().
            model = filly.Model(obj.Owner, filly_mex('Scene.add', obj.Id, asset.Id));
        end

        function model = createMesh(obj, positions, indices, varargin)
            % A model from N x 3 positions and M x 3 triangle indices (1-based, counterclockwise
            % front faces). Options: 'Normals' (N x 3), 'Uvs' (N x 2), 'Colors' (N x 4),
            % 'BaseColor' ([1 1 1 1]), 'Metallic' (0), 'Roughness' (1), 'Emissive' ([0 0 0]),
            % 'Unlit' (false), 'DoubleSided' (false), 'AlphaMode' ('opaque', 'mask', 'blend').
            % It is renderer.createMeshAsset() and add() in one step, without keeping the asset.
            args = filly.internal.meshArguments(positions, indices, varargin);
            model = filly.Model(obj.Owner, filly_mex('Scene.createMesh', obj.Id, args{:}));
        end

        function light = addDirectionalLight(obj, direction, varargin)
            % Options: 'Intensity' (50000 lux), 'Color' ([1 1 1]).
            o = filly.internal.options(struct('Intensity', 50000, 'Color', [1 1 1]), varargin);
            light = filly.Light(obj.Owner, filly_mex('Scene.addDirectionalLight', obj.Id, direction, o.Intensity, o.Color));
        end

        function light = addSunLight(obj, direction, varargin)
            % Options: 'Intensity' (100000 lux), 'Color', 'AngularRadiusDeg' (0.545),
            % 'HaloSize' (10), 'HaloFalloff' (80).
            o = filly.internal.options(struct('Intensity', 100000, 'Color', [1 1 1], 'AngularRadiusDeg', 0.545, ...
                'HaloSize', 10, 'HaloFalloff', 80), varargin);
            light = filly.Light(obj.Owner, filly_mex('Scene.addSunLight', obj.Id, direction, o.Intensity, o.Color, ...
                o.AngularRadiusDeg, o.HaloSize, o.HaloFalloff));
        end

        function light = addPointLight(obj, position, varargin)
            % Options: 'Intensity' (50000 cd), 'Color', 'Range' (10).
            o = filly.internal.options(struct('Intensity', 50000, 'Color', [1 1 1], 'Range', 10), varargin);
            light = filly.Light(obj.Owner, filly_mex('Scene.addPointLight', obj.Id, position, o.Intensity, o.Color, o.Range));
        end

        function light = addSpotLight(obj, position, direction, varargin)
            % Options: 'Intensity' (50000 cd), 'Color', 'Range' (10), 'ConeDeg' ([15 30],
            % inner and outer half-angles).
            o = filly.internal.options(struct('Intensity', 50000, 'Color', [1 1 1], 'Range', 10, 'ConeDeg', [15 30]), varargin);
            light = filly.Light(obj.Owner, filly_mex('Scene.addSpotLight', obj.Id, position, direction, o.Intensity, ...
                o.Color, o.Range, o.ConeDeg));
        end

        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
        function v = get.camera(obj), v = filly.Camera(obj.Owner, filly_mex('get', obj.Id, 'camera')); end
        function set.camera(obj, v), filly_mex('set', obj.Id, 'camera', v.Id); end
        function v = get.encoding(obj), v = filly_mex('get', obj.Id, 'encoding'); end
        function set.encoding(obj, v), filly_mex('set', obj.Id, 'encoding', filly.internal.text(v)); end
        function v = get.outputPath(obj), v = filly_mex('get', obj.Id, 'outputPath'); end
        function set.outputPath(obj, v), filly_mex('set', obj.Id, 'outputPath', filly.internal.text(v)); end
        function v = get.toneMapping(obj), v = filly_mex('get', obj.Id, 'toneMapping'); end
        function set.toneMapping(obj, v), filly_mex('set', obj.Id, 'toneMapping', filly.internal.text(v)); end
        function v = get.antialiasing(obj), v = filly_mex('get', obj.Id, 'antialiasing'); end
        function set.antialiasing(obj, v), filly_mex('set', obj.Id, 'antialiasing', filly.internal.text(v)); end
        function v = get.msaa(obj), v = filly_mex('get', obj.Id, 'msaa'); end
        function set.msaa(obj, v), filly_mex('set', obj.Id, 'msaa', v); end
        function v = get.shadows(obj), v = filly_mex('get', obj.Id, 'shadows'); end
        function set.shadows(obj, v), filly_mex('set', obj.Id, 'shadows', v); end
        function v = get.transparent(obj), v = filly_mex('get', obj.Id, 'transparent'); end
        function set.transparent(obj, v), filly_mex('set', obj.Id, 'transparent', v); end
        function v = get.dithering(obj), v = filly_mex('get', obj.Id, 'dithering'); end
        function set.dithering(obj, v), filly_mex('set', obj.Id, 'dithering', v); end
        function v = get.ssao(obj), v = filly_mex('get', obj.Id, 'ssao'); end
        function set.ssao(obj, v), filly_mex('set', obj.Id, 'ssao', v); end
        function v = get.bloom(obj), v = filly_mex('get', obj.Id, 'bloom'); end
        function set.bloom(obj, v), filly_mex('set', obj.Id, 'bloom', v); end
        function v = get.fog(obj), v = filly_mex('get', obj.Id, 'fog'); end
        function set.fog(obj, v), filly_mex('set', obj.Id, 'fog', v); end
        function v = get.fogColor(obj), v = filly_mex('get', obj.Id, 'fogColor'); end
        function set.fogColor(obj, v), filly_mex('set', obj.Id, 'fogColor', v); end
        function v = get.fogDensity(obj), v = filly_mex('get', obj.Id, 'fogDensity'); end
        function set.fogDensity(obj, v), filly_mex('set', obj.Id, 'fogDensity', v); end
        function v = get.fogStart(obj), v = filly_mex('get', obj.Id, 'fogStart'); end
        function set.fogStart(obj, v), filly_mex('set', obj.Id, 'fogStart', v); end
        function v = get.depthOfField(obj), v = filly_mex('get', obj.Id, 'depthOfField'); end
        function set.depthOfField(obj, v), filly_mex('set', obj.Id, 'depthOfField', v); end
        function v = get.vignette(obj), v = filly_mex('get', obj.Id, 'vignette'); end
        function set.vignette(obj, v), filly_mex('set', obj.Id, 'vignette', v); end
        function v = get.vignetteMidpoint(obj), v = filly_mex('get', obj.Id, 'vignetteMidpoint'); end
        function set.vignetteMidpoint(obj, v), filly_mex('set', obj.Id, 'vignetteMidpoint', v); end
        function v = get.vignetteRoundness(obj), v = filly_mex('get', obj.Id, 'vignetteRoundness'); end
        function set.vignetteRoundness(obj, v), filly_mex('set', obj.Id, 'vignetteRoundness', v); end
        function v = get.vignetteFeather(obj), v = filly_mex('get', obj.Id, 'vignetteFeather'); end
        function set.vignetteFeather(obj, v), filly_mex('set', obj.Id, 'vignetteFeather', v); end
        function v = get.vignetteColor(obj), v = filly_mex('get', obj.Id, 'vignetteColor'); end
        function set.vignetteColor(obj, v), filly_mex('set', obj.Id, 'vignetteColor', v); end
        function v = get.environment(obj)
            id = filly_mex('get', obj.Id, 'environment');
            if isempty(id), v = []; else, v = filly.Environment(obj.Owner, id); end
        end
        function set.environment(obj, v)
            if isempty(v), filly_mex('set', obj.Id, 'environment', []);
            else, filly_mex('set', obj.Id, 'environment', v.Id); end
        end
        function v = get.environmentIntensity(obj), v = filly_mex('get', obj.Id, 'environmentIntensity'); end
        function set.environmentIntensity(obj, v), filly_mex('set', obj.Id, 'environmentIntensity', v); end
        function v = get.environmentRotationDeg(obj), v = filly_mex('get', obj.Id, 'environmentRotationDeg'); end
        function set.environmentRotationDeg(obj, v), filly_mex('set', obj.Id, 'environmentRotationDeg', v); end
        function v = get.environmentVisible(obj), v = filly_mex('get', obj.Id, 'environmentVisible'); end
        function set.environmentVisible(obj, v), filly_mex('set', obj.Id, 'environmentVisible', v); end
        function v = get.background(obj), v = filly_mex('get', obj.Id, 'background'); end
        function set.background(obj, v), filly_mex('set', obj.Id, 'background', v); end
        function v = get.pickBlended(obj), v = filly_mex('get', obj.Id, 'pickBlended'); end
        function set.pickBlended(obj, v), filly_mex('set', obj.Id, 'pickBlended', v); end
    end
end
