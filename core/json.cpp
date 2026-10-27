#include "json.h"

#include <charconv>
#include <cmath>
#include <cstdint>
#include <stdexcept>

namespace filly::json {
namespace {

// Deeper documents are not glTF; the limit keeps malformed input from exhausting the stack.
constexpr int max_depth = 256;

class Parser {
public:
    explicit Parser(std::string_view text) : text_(text) {}
    Value document() {
        Value value = parse(0);
        space();
        if (pos_ != text_.size()) fail("trailing characters");
        return value;
    }

private:
    [[noreturn]] void fail(const char* what) const {
        throw std::invalid_argument(std::string("JSON ") + what + " at byte " + std::to_string(pos_));
    }
    void space() {
        while (pos_ < text_.size() && (text_[pos_] == ' ' || text_[pos_] == '\t' || text_[pos_] == '\n' || text_[pos_] == '\r'))
            ++pos_;
    }
    bool take(char c) {
        space();
        if (pos_ < text_.size() && text_[pos_] == c) { ++pos_; return true; }
        return false;
    }
    void expect(char c) { if (!take(c)) fail("syntax error"); }
    bool literal(std::string_view word) {
        if (text_.substr(pos_, word.size()) != word) return false;
        pos_ += word.size();
        return true;
    }
    unsigned hex4() {
        if (pos_ + 4 > text_.size()) fail("escape");
        unsigned value = 0;
        for (int i = 0; i < 4; ++i) {
            const char c = text_[pos_++];
            value <<= 4;
            if (c >= '0' && c <= '9') value |= unsigned(c - '0');
            else if (c >= 'a' && c <= 'f') value |= unsigned(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') value |= unsigned(c - 'A' + 10);
            else fail("escape");
        }
        return value;
    }
    static void utf8(std::string& out, uint32_t cp) {
        if (cp < 0x80) out += char(cp);
        else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) {
            out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F));
        } else {
            out += char(0xF0 | (cp >> 18)); out += char(0x80 | ((cp >> 12) & 0x3F));
            out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F));
        }
    }
    std::string string() {
        expect('"');
        std::string out;
        while (true) {
            if (pos_ >= text_.size()) fail("unterminated string");
            const char c = text_[pos_++];
            if (c == '"') return out;
            if (static_cast<unsigned char>(c) < 0x20) fail("control character in string");
            if (c != '\\') { out += c; continue; }
            if (pos_ >= text_.size()) fail("escape");
            switch (text_[pos_++]) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    uint32_t cp = hex4();
                    if (cp >= 0xD800 && cp < 0xDC00 && text_.substr(pos_, 2) == "\\u") {
                        pos_ += 2;
                        const uint32_t low = hex4();
                        if (low < 0xDC00 || low >= 0xE000) fail("surrogate pair");
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                    }
                    utf8(out, cp);
                    break;
                }
                default: fail("escape");
            }
        }
    }
    Value parse(int depth) {
        if (depth > max_depth) fail("nesting too deep");
        space();
        if (pos_ >= text_.size()) fail("unexpected end");
        Value value;
        const char c = text_[pos_];
        if (c == '{') {
            ++pos_;
            value.type = Value::Type::object;
            if (take('}')) return value;
            do {
                space();
                value.keys.push_back(string());
                expect(':');
                value.items.push_back(parse(depth + 1));
            } while (take(','));
            expect('}');
        } else if (c == '[') {
            ++pos_;
            value.type = Value::Type::array;
            if (take(']')) return value;
            do value.items.push_back(parse(depth + 1)); while (take(','));
            expect(']');
        } else if (c == '"') {
            value.type = Value::Type::string;
            value.text = string();
        } else if (literal("true")) {
            value.type = Value::Type::boolean;
            value.boolean = true;
        } else if (literal("false")) {
            value.type = Value::Type::boolean;
        } else if (literal("null")) {
        } else {
            const size_t start = pos_;
            if (text_[pos_] == '-') ++pos_;
            while (pos_ < text_.size() && std::string_view("0123456789.eE+-").find(text_[pos_]) != std::string_view::npos)
                ++pos_;
            value.type = Value::Type::number;
            value.text = std::string(text_.substr(start, pos_ - start));
            const auto* first = value.text.data();
            const auto* last = first + value.text.size();
            const auto result = std::from_chars(first, last, value.number);
            // from_chars reports out-of-range values; JSON parsers give them infinity.
            if (result.ec == std::errc::result_out_of_range) value.number = value.text[0] == '-' ? -HUGE_VAL : HUGE_VAL;
            else if (result.ec != std::errc() || result.ptr != last) fail("number");
        }
        return value;
    }

    std::string_view text_;
    size_t pos_ = 0;
};

void write_string(std::string& out, const std::string& text) {
    out += '"';
    for (const char c : text) {
        const auto byte = static_cast<unsigned char>(c);
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (byte < 0x20) {
            static const char digits[] = "0123456789abcdef";
            out += "\\u00";
            out += digits[byte >> 4];
            out += digits[byte & 15];
        } else out += c;
    }
    out += '"';
}

void write(std::string& out, const Value& value) {
    switch (value.type) {
        case Value::Type::null: out += "null"; break;
        case Value::Type::boolean: out += value.boolean ? "true" : "false"; break;
        case Value::Type::number: out += value.text; break;
        case Value::Type::string: write_string(out, value.text); break;
        case Value::Type::array:
            out += '[';
            for (size_t i = 0; i < value.items.size(); ++i) {
                if (i) out += ',';
                write(out, value.items[i]);
            }
            out += ']';
            break;
        case Value::Type::object:
            out += '{';
            for (size_t i = 0; i < value.items.size(); ++i) {
                if (i) out += ',';
                write_string(out, value.keys[i]);
                out += ':';
                write(out, value.items[i]);
            }
            out += '}';
            break;
    }
}
}

const Value* Value::find(std::string_view key) const {
    if (type != Type::object) return nullptr;
    for (size_t i = 0; i < keys.size(); ++i) if (keys[i] == key) return &items[i];
    return nullptr;
}
Value* Value::find(std::string_view key) {
    return const_cast<Value*>(static_cast<const Value*>(this)->find(key));
}
Value& Value::at(std::string_view key) {
    if (type == Type::null) type = Type::object;
    if (auto* found = find(key)) return *found;
    keys.emplace_back(key);
    items.emplace_back();
    return items.back();
}
void Value::erase(std::string_view key) {
    for (size_t i = 0; i < keys.size(); ++i) {
        if (keys[i] != key) continue;
        keys.erase(keys.begin() + ptrdiff_t(i));
        items.erase(items.begin() + ptrdiff_t(i));
        return;
    }
}
bool Value::is_index() const {
    return type == Type::number && number >= 0 && number <= 9007199254740992.0 && std::floor(number) == number;
}

Value object() { Value v; v.type = Value::Type::object; return v; }
Value array() { Value v; v.type = Value::Type::array; return v; }
Value string(std::string text) { Value v; v.type = Value::Type::string; v.text = std::move(text); return v; }
Value number(double value) {
    if (!std::isfinite(value)) throw std::invalid_argument("JSON numbers must be finite");
    Value v;
    v.type = Value::Type::number;
    v.number = value;
    char buffer[32];
    // The shortest text that parses back to the same double.
    const auto result = std::to_chars(buffer, buffer + sizeof(buffer), value);
    v.text.assign(buffer, result.ptr);
    return v;
}
Value number(float value) { return number(double(value)); }

Value parse(std::string_view text) { return Parser(text).document(); }
std::string write(const Value& value) {
    std::string out;
    write(out, value);
    return out;
}

}
