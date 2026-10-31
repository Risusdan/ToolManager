// Platform-independent launcher logic (see docs/DESIGN.md §6.2).
//
// Install layout next to the launcher:
//   ToolManager.exe            this launcher; tiny and rarely updated
//   app-1.2.0/                 one complete ToolManager build per version
//     .complete                written last by the installer / updater
//     ToolManagerApp.exe
//   app-1.3.0/
//     .disabled                optional: skip this version (set when it is known bad)
//
// The launcher never deletes or modifies anything; it only picks a folder and starts it.
#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace launcher {

inline constexpr const char* kAppPrefix = "app-";
inline constexpr const char* kCompleteMarker = ".complete";
inline constexpr const char* kDisabledMarker = ".disabled";
inline constexpr const char* kAppExe = "ToolManagerApp.exe";

struct Version {
    unsigned long major = 0, minor = 0, patch = 0;
    auto operator<=>(const Version&) const = default;
    std::string str() const;
};

// Parses "1.2.3" (MAJOR.MINOR.PATCH, digits only, no leading zeros except "0").
std::optional<Version> parse_version(const std::string& text);

struct Candidate {
    Version version;
    std::filesystem::path dir;
    std::string skip_reason;  // empty when usable
};

// All app-x.y.z folders under root, newest first, each with the reason it can't be used (if any).
std::vector<Candidate> scan(const std::filesystem::path& root, const std::string& exe_name = kAppExe);

// Newest usable candidate, if any.
std::optional<Candidate> pick(const std::vector<Candidate>& candidates);

// Arguments part of a Windows command line, i.e. without the program name, following the
// CommandLineToArgvW rules for argv[0] (quoted, or up to the first space/tab).
std::wstring args_after_program(const std::wstring& command_line);

// Quote a path for a Windows command line.
std::wstring quote_arg(const std::wstring& arg);

}  // namespace launcher
