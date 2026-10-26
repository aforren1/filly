classdef (Abstract) Object < handle
    % Base class of the filly classes: a handle into the table of the filly_mex gateway.
    %
    % Every object that a renderer creates keeps a reference to that filly.Renderer, so that
    % the renderer stays open while the object exists. Deleting a filly object drops the
    % gateway's reference to the native object; it does not close it, as in the Python API.

    properties (SetAccess = protected, Hidden)
        Id = uint64(0)
        Owner = []
    end

    methods
        function obj = Object(owner, id)
            % Public, not protected: Octave 8 rejects the implicit call of a protected
            % superclass constructor from a subclass constructor. The class is abstract.
            if nargin > 0
                obj.Owner = owner;
                obj.Id = id;
            end
        end

        function delete(obj)
            if obj.Id ~= 0
                % An error in a destructor would only be printed; the gateway ignores
                % handles that it no longer has, so the Id is not cleared: Octave 8 rejects
                % that assignment in a destructor that runs during `clear functions`.
                try
                    filly_mex('release', obj.Id);
                catch
                end
            end
        end

        function tf = eq(a, b)
            % True if both refer to the same native object.
            tf = isa(a, 'filly.Object') && isa(b, 'filly.Object') && isscalar(a) && isscalar(b) ...
                && a.Id ~= 0 && b.Id ~= 0 && filly_mex('same', a.Id, b.Id);
        end

        function tf = ne(a, b)
            tf = ~eq(a, b);
        end
    end

    methods (Static, Hidden)
        function v = ids(objects)
            % The gateway handles of an object array, for the batch calls.
            if isempty(objects)
                v = zeros(1, 0, 'uint64');
            else
                v = [objects.Id];
            end
        end

        function objects = wrap(constructor, owner, ids)
            % An object array of one class from a vector of handles. Octave cannot concatenate
            % classdef objects ([a b]) and has no Class.empty, but it can grow an array by
            % indexed assignment. An object with handle 0 owns nothing, so it seeds the array.
            objects = constructor(owner, uint64(0));
            objects = objects(zeros(1, 0));
            for i = 1:numel(ids)
                objects(i) = constructor(owner, ids(i));
            end
        end
    end
end
