classdef Asset < filly.Object
    % ASSET  An asset that scene.add() instantiates, from renderer.loadAsset() or
    %   renderer.createMeshAsset().
    %   close() releases the source data; models already made from it stay.

    properties (Dependent, SetAccess = private)
        memory
        closed
    end

    methods
        function obj = Asset(owner, id)
            obj@filly.Object(owner, id);
        end

        function close(obj)
            filly_mex('Asset.close', obj.Id);
        end

        function v = get.memory(obj), v = filly_mex('get', obj.Id, 'memory'); end
        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
