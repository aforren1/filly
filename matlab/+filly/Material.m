classdef Material < filly.Object
    % MATERIAL  A glTF metallic-roughness material of a model.
    %   Texture slots take a filly.Texture, a filly.ImportedTexture, or [] to remove the
    %   assignment. The texture transform properties are [] for a slot without a texture.

    properties (Dependent)
        baseColor
        metallic
        roughness
        emissive
        baseColorTexture
        emissiveTexture
        baseColorTextureOffset
        baseColorTextureScale
        baseColorTextureRotationDeg
        emissiveTextureOffset
        emissiveTextureScale
        emissiveTextureRotationDeg
    end

    methods
        function obj = Material(owner, id)
            obj@filly.Object(owner, id);
        end

        function setParameters(materials, name, values)
            % Set one parameter of an array of N materials from row i of values, in one call:
            % 'baseColor' (N x 4), 'emissive' (N x 3), 'metallic' or 'roughness' (N values).
            % The materials may come from any models of one renderer. Inputs are checked
            % first, so an error changes nothing.
            filly_mex('Material.setParameters', filly.Object.ids(materials), filly.internal.text(name), values);
        end

        function v = get.baseColor(obj), v = filly_mex('get', obj.Id, 'baseColor'); end
        function set.baseColor(obj, v), filly_mex('set', obj.Id, 'baseColor', v); end
        function v = get.metallic(obj), v = filly_mex('get', obj.Id, 'metallic'); end
        function set.metallic(obj, v), filly_mex('set', obj.Id, 'metallic', v); end
        function v = get.roughness(obj), v = filly_mex('get', obj.Id, 'roughness'); end
        function set.roughness(obj, v), filly_mex('set', obj.Id, 'roughness', v); end
        function v = get.emissive(obj), v = filly_mex('get', obj.Id, 'emissive'); end
        function set.emissive(obj, v), filly_mex('set', obj.Id, 'emissive', v); end
        function v = get.baseColorTexture(obj), v = obj.texture(0); end
        function set.baseColorTexture(obj, v), obj.setTexture(0, v); end
        function v = get.emissiveTexture(obj), v = obj.texture(1); end
        function set.emissiveTexture(obj, v), obj.setTexture(1, v); end
        function v = get.baseColorTextureOffset(obj), v = filly_mex('get', obj.Id, 'baseColorTextureOffset'); end
        function set.baseColorTextureOffset(obj, v), filly_mex('set', obj.Id, 'baseColorTextureOffset', v); end
        function v = get.baseColorTextureScale(obj), v = filly_mex('get', obj.Id, 'baseColorTextureScale'); end
        function set.baseColorTextureScale(obj, v), filly_mex('set', obj.Id, 'baseColorTextureScale', v); end
        function v = get.baseColorTextureRotationDeg(obj), v = filly_mex('get', obj.Id, 'baseColorTextureRotationDeg'); end
        function set.baseColorTextureRotationDeg(obj, v), filly_mex('set', obj.Id, 'baseColorTextureRotationDeg', v); end
        function v = get.emissiveTextureOffset(obj), v = filly_mex('get', obj.Id, 'emissiveTextureOffset'); end
        function set.emissiveTextureOffset(obj, v), filly_mex('set', obj.Id, 'emissiveTextureOffset', v); end
        function v = get.emissiveTextureScale(obj), v = filly_mex('get', obj.Id, 'emissiveTextureScale'); end
        function set.emissiveTextureScale(obj, v), filly_mex('set', obj.Id, 'emissiveTextureScale', v); end
        function v = get.emissiveTextureRotationDeg(obj), v = filly_mex('get', obj.Id, 'emissiveTextureRotationDeg'); end
        function set.emissiveTextureRotationDeg(obj, v), filly_mex('set', obj.Id, 'emissiveTextureRotationDeg', v); end
    end

    methods (Access = private)
        function v = texture(obj, slot)
            [id, kind] = filly_mex('Material.texture', obj.Id, slot);
            if isempty(id)
                v = [];
            elseif strcmp(kind, 'ImportedTexture')
                v = filly.ImportedTexture(obj.Owner, id);
            else
                v = filly.Texture(obj.Owner, id);
            end
        end

        function setTexture(obj, slot, v)
            if isempty(v)
                filly_mex('Material.setTexture', obj.Id, slot, []);
            else
                filly_mex('Material.setTexture', obj.Id, slot, v.Id);
            end
        end
    end
end
