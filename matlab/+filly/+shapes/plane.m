function shape = plane(varargin)
% PLANE  A rectangle in the XY plane, centered on the origin, facing +Z.
%   Options: 'Width' (1), 'Height' (1), 'Segments' ([columns rows], [1 1]).
%   shape has positions (N x 3), normals (N x 3), uvs (N x 2), and indices (M x 3, 1-based),
%   for scene.createMesh(shape.positions, shape.indices, 'Normals', shape.normals, ...).
o = filly.internal.options(struct('Width', 1, 'Height', 1, 'Segments', [1 1]), varargin);
shape = filly_mex('shape', 'plane', o.Width, o.Height, o.Segments(1), o.Segments(2));
end
