#pragma once
// A small JSON document model for the glTF extensions that cgltf keeps as text, and for the
// one document rewrite that remains (EXT_mesh_gpu_instancing). Numbers keep their source text,
// so a rewrite changes only the values that it edits.

#include <string>
#include <string_view>
#include <vector>

namespace filly::json {

struct Value {
    enum class Type { null, boolean, number, string, array, object };
    Type type = Type::null;
    bool boolean = false;
    double number = 0;
    // The decoded text of a string, or the source text of a number.
    std::string text;
    // Array items, or object values in source order.
    std::vector<Value> items;
    // Object keys, parallel to items.
    std::vector<std::string> keys;

    bool is_object() const { return type == Type::object; }
    bool is_array() const { return type == Type::array; }
    bool is_number() const { return type == Type::number; }
    bool is_string() const { return type == Type::string; }
    // Null when this is not an object or has no such key.
    const Value* find(std::string_view key) const;
    Value* find(std::string_view key);
    // Adds the key when it is missing. A null value becomes an empty object first.
    Value& at(std::string_view key);
    void erase(std::string_view key);
    // True for a number with an integer value in [0, 2^53].
    bool is_index() const;
};

Value object();
Value array();
Value string(std::string text);
// Writes the value with max_digits10 precision, so that a float32 or float64 parses back exactly.
Value number(double value);
Value number(float value);

// Throws std::invalid_argument with a short reason.
Value parse(std::string_view text);
std::string write(const Value& value);

}
