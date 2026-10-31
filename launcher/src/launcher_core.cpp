#include "launcher_core.hpp"

#include <algorithm>
#include <cctype>
#include <system_error>

namespace fs = std::filesystem;

namespace launcher {

std::string Version::str() const {
    return std::to_string(major) + "." + std::to_string(minor) + "." + std::to_string(patch);
}

std::optional<Version> parse_version(const std::string& text) {
    unsigned long parts[3] = {0, 0, 0};
    size_t pos = 0;
    for (int i = 0; i < 3; ++i) {
        size_t start = pos;
        while (pos < text.size() && std::isdigit(static_cast<unsigned char>(text[pos]))) ++pos;
        size_t len = pos - start;
        if (len == 0 || len > 9) return std::nullopt;               // empty or absurdly long
        if (len > 1 && text[start] == '0') return std::nullopt;     // leading zero
        parts[i] = std::stoul(text.substr(start, len));
        if (i < 2) {
            if (pos >= text.size() || text[pos] != '.') return std::nullopt;
            ++pos;
        }
    }
    if (pos != text.size()) return std::nullopt;
    return Version{parts[0], parts[1], parts[2]};
}

std::vector<Candidate> scan(const fs::path& root, const std::string& exe_name) {
    std::vector<Candidate> result;
    std::error_code ec;
    for (fs::directory_iterator it(root, ec), end; !ec && it != end; it.increment(ec)) {
        const fs::path dir = it->path();
        // Compare as wide/native text: converting arbitrary (e.g. Chinese) folder names to a
        // narrow string can throw on Windows. Only ASCII names can be app-x.y.z anyway.
        const auto native = dir.filename().native();
        std::string name;
        bool ascii = true;
        for (auto ch : native) {
            if (static_cast<unsigned long>(ch) > 0x7F) { ascii = false; break; }
            name += static_cast<char>(ch);
        }
        if (!ascii || name.rfind(kAppPrefix, 0) != 0) continue;
        // Separate error code: a failing check must skip this entry, not end the iteration.
        std::error_code check;
        if (!it->is_directory(check) || it->is_symlink(check)) continue;
        auto version = parse_version(name.substr(std::char_traits<char>::length(kAppPrefix)));
        if (!version) continue;

        Candidate c{*version, dir, ""};
        if (fs::exists(dir / kDisabledMarker, check)) {
            c.skip_reason = "disabled";
        } else if (!fs::is_regular_file(dir / kCompleteMarker, check)) {
            c.skip_reason = "incomplete (no .complete marker)";
        } else if (!fs::is_regular_file(dir / exe_name, check)) {
            c.skip_reason = "missing " + exe_name;
        }
        result.push_back(std::move(c));
    }
    std::sort(result.begin(), result.end(),
              [](const Candidate& a, const Candidate& b) { return a.version > b.version; });
    return result;
}

std::optional<Candidate> pick(const std::vector<Candidate>& candidates) {
    for (const auto& c : candidates) {
        if (c.skip_reason.empty()) return c;
    }
    return std::nullopt;
}

std::wstring args_after_program(const std::wstring& cl) {
    size_t i = 0;
    if (i < cl.size() && cl[i] == L'"') {
        // argv[0] in quotes: ends at the next quote (no escaping applies to argv[0]).
        ++i;
        while (i < cl.size() && cl[i] != L'"') ++i;
        if (i < cl.size()) ++i;
    } else {
        while (i < cl.size() && cl[i] != L' ' && cl[i] != L'\t') ++i;
    }
    while (i < cl.size() && (cl[i] == L' ' || cl[i] == L'\t')) ++i;
    return cl.substr(i);
}

std::wstring quote_arg(const std::wstring& arg) {
    if (!arg.empty() && arg.find_first_of(L" \t\"") == std::wstring::npos) return arg;
    std::wstring out = L"\"";
    size_t backslashes = 0;
    for (wchar_t ch : arg) {
        if (ch == L'\\') {
            ++backslashes;
        } else if (ch == L'"') {
            out.append(backslashes * 2 + 1, L'\\');
            out += L'"';
            backslashes = 0;
        } else {
            out.append(backslashes, L'\\');
            out += ch;
            backslashes = 0;
        }
    }
    out.append(backslashes * 2, L'\\');  // backslashes before the closing quote
    out += L'"';
    return out;
}

}  // namespace launcher
