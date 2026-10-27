// Vertex arrays for simple shapes. The arithmetic follows the NumPy version these replace:
// values are computed in double and stored as float32, with linspace's i * step spacing.

#include "renderer.h"

#include <cmath>
#include <stdexcept>

namespace filly {
namespace {

constexpr double pi = 3.141592653589793;

// numpy.linspace(start, stop, count): start + i * step, with the last value exact.
std::vector<double> linspace(double start, double stop, size_t count) {
    std::vector<double> values(count);
    const double step = (stop - start) / double(count - 1);
    for (size_t i = 0; i < count; ++i) values[i] = double(i) * step + start;
    values.back() = stop;
    return values;
}

// Two triangles per cell of a (rows + 1) x (columns + 1) vertex grid, rows from the top.
void grid(std::vector<uint32_t>& indices, size_t columns, size_t rows, uint32_t base = 0) {
    for (size_t r = 0; r < rows; ++r)
        for (size_t c = 0; c < columns; ++c) {
            const auto a = uint32_t(base + r * (columns + 1) + c), b = uint32_t(a + columns + 1);
            indices.insert(indices.end(), {a, b, a + 1, a + 1, b, b + 1});
        }
}

void push(std::vector<float>& out, std::initializer_list<double> values) {
    for (double value : values) out.push_back(float(value));
}

}

ShapeArrays shape_plane(double width, double height, int64_t columns, int64_t rows) {
    if (columns < 1 || rows < 1 || !(width > 0 && height > 0))
        throw std::invalid_argument("plane needs positive width and height and at least one segment each way");
    ShapeArrays shape;
    const auto u = linspace(0, 1, size_t(columns) + 1), v = linspace(0, 1, size_t(rows) + 1);
    for (double y : v)
        for (double x : u) {
            push(shape.positions, {(x - 0.5) * width, (0.5 - y) * height, 0});
            push(shape.normals, {0, 0, 1});
            push(shape.uvs, {x, y});
        }
    grid(shape.indices, size_t(columns), size_t(rows));
    return shape;
}

ShapeArrays shape_box(double width, double height, double depth) {
    if (!(width > 0 && height > 0 && depth > 0)) throw std::invalid_argument("box needs positive width, height, and depth");
    const double half[] = {width / 2, height / 2, depth / 2};
    // Outward normal, then the face's right and up directions; right x up = normal.
    static const double faces[6][3][3] = {
        {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}}, {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}},
        {{1, 0, 0}, {0, 0, -1}, {0, 1, 0}}, {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},
        {{0, 1, 0}, {1, 0, 0}, {0, 0, -1}}, {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}}};
    static const double corners[4][4] = {{-1, 1, 0, 0}, {1, 1, 1, 0}, {-1, -1, 0, 1}, {1, -1, 1, 1}};
    ShapeArrays shape;
    for (uint32_t face = 0; face < 6; ++face) {
        const auto& [n, r, up] = faces[face];
        for (const auto& [x, y, s, t] : corners) {
            for (int axis = 0; axis < 3; ++axis)
                shape.positions.push_back(float((n[axis] + x * r[axis] + y * up[axis]) * half[axis]));
            push(shape.normals, {n[0], n[1], n[2]});
            push(shape.uvs, {s, t});
        }
        const uint32_t base = 4 * face;
        shape.indices.insert(shape.indices.end(), {base + 2, base + 3, base + 1, base + 2, base + 1, base});
    }
    return shape;
}

ShapeArrays shape_uv_sphere(double radius, int64_t segments, int64_t rings) {
    if (segments < 3 || rings < 2 || !(radius > 0))
        throw std::invalid_argument("uv_sphere needs radius > 0, segments >= 3, and rings >= 2");
    ShapeArrays shape;
    const auto u = linspace(0, 1, size_t(segments) + 1), v = linspace(0, 1, size_t(rings) + 1);
    for (double y : v)
        for (double x : u) {
            const double theta = x * 2 * pi, phi = y * pi;
            const double normal[] = {std::sin(phi) * std::sin(theta), std::cos(phi), std::sin(phi) * std::cos(theta)};
            push(shape.positions, {normal[0] * radius, normal[1] * radius, normal[2] * radius});
            push(shape.normals, {normal[0], normal[1], normal[2]});
            push(shape.uvs, {x, y});
        }
    std::vector<uint32_t> all;
    grid(all, size_t(segments), size_t(rings));
    // The first triangle of each top cell and the second of each bottom cell have zero area.
    for (size_t r = 0; r < size_t(rings); ++r)
        for (size_t s = 0; s < size_t(segments); ++s)
            for (size_t k = 0; k < 2; ++k) {
                if ((r == 0 && k == 0) || (r + 1 == size_t(rings) && k == 1)) continue;
                const auto* first = all.data() + ((r * size_t(segments) + s) * 2 + k) * 3;
                shape.indices.insert(shape.indices.end(), first, first + 3);
            }
    return shape;
}

ShapeArrays shape_cylinder(double radius, double height, int64_t segments, bool caps) {
    if (segments < 3 || !(radius > 0 && height > 0))
        throw std::invalid_argument("cylinder needs radius > 0, height > 0, and segments >= 3");
    ShapeArrays shape;
    const auto u = linspace(0, 1, size_t(segments) + 1);
    for (double y : {0.0, 1.0})
        for (double x : u) {
            const double theta = x * 2 * pi;
            const double normal[] = {std::sin(theta), 0, std::cos(theta)};
            push(shape.positions, {normal[0] * radius + 0, normal[1] * 0 + (0.5 - y) * height, normal[2] * radius + 0});
            push(shape.normals, {normal[0], normal[1], normal[2]});
            push(shape.uvs, {x, y});
        }
    grid(shape.indices, size_t(segments), 1);
    if (!caps) return shape;
    const auto angles = linspace(0, 2 * pi, size_t(segments) + 1);
    for (double sign : {1.0, -1.0}) {
        const auto start = uint32_t(shape.positions.size() / 3);
        push(shape.positions, {0, sign * height / 2, 0});
        push(shape.normals, {0, sign, 0});
        push(shape.uvs, {0.5, 0.5});
        for (size_t j = 0; j < size_t(segments); ++j) {
            const double s = std::sin(angles[j]), c = std::cos(angles[j]);
            push(shape.positions, {s * radius, sign * height / 2, c * radius});
            push(shape.normals, {0, sign, 0});
            push(shape.uvs, {0.5 + 0.5 * s * 1, 0.5 + 0.5 * c * -sign});
        }
        for (uint32_t j = 0; j < uint32_t(segments); ++j) {
            const uint32_t a = start + 1 + j, b = start + 1 + (j + 1) % uint32_t(segments);
            // Viewed from outside the cap, (center, a, b) winds counterclockwise on top.
            if (sign > 0) shape.indices.insert(shape.indices.end(), {start, a, b});
            else shape.indices.insert(shape.indices.end(), {start, b, a});
        }
    }
    return shape;
}

}
