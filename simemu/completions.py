"""
Shell completion generators for simemu CLI.

Usage:
  eval "$(simemu completions zsh)"
  eval "$(simemu completions bash)"
"""


def zsh_completion() -> str:
    """Generate zsh completion script."""
    return '''#compdef simemu

_simemu() {
    local -a commands
    commands=(
        'claim:Claim a device session'
        'do:Execute a command on a session'
        'sessions:List active sessions'
        'status:System health overview'
        'config:Configure settings'
        'serve:Start HTTP API server'
        'daemon:Manage background daemon'
        'maintenance:Toggle maintenance mode'
        'menubar:Launch menu bar app'
        'create:Create a new simulator/emulator'
        'idle-shutdown:Shut down idle simulators'
        'rename:Rename a simulator by session or device'
        'relabel:Assign a persistent alias to a real device'
    )

    local -a platforms
    platforms=(
        'ios:iOS Simulator'
        'android:Android Emulator'
        'macos:macOS native'
        'iphone:iOS phone (alias)'
        'ipad:iOS tablet (alias)'
        'pixel:Android phone (alias)'
        'watch:watchOS (alias)'
        'tv:tvOS Apple TV (alias)'
        'appletv:tvOS Apple TV (alias)'
        'vision:visionOS (alias)'
        'mac:macOS (alias)'
    )

    local -a do_commands
    do_commands=(
        'install:Install app' 'launch:Launch app' 'terminate:Stop app'
        'uninstall:Remove app' 'reset-app:Reset app data'
        'tap:Tap at coordinates' 'swipe:Swipe gesture' 'long-press:Long press'
        'key:Press hardware key' 'input:Type text' 'a11y-tap:Tap by label'
        'screenshot:Capture screen' 'proof:Verified proof capture'
        'url:Open URL/deep link' 'maestro:Run Maestro flow'
        'appearance:Set light/dark' 'rotate:Set orientation'
        'status-bar:Override status bar' 'boot:Wake device'
        'show:Show window' 'hide:Hide window' 'done:Release session'
        'renew:Extend session' 'help:List all commands'
        'build:Build app' 'env:Device environment info'
        'dismiss-alert:Dismiss alert' 'accept-alert:Accept alert'
        'video-start:Start recording' 'video-stop:Stop recording'
        'present:Present simulator' 'stabilize:Stabilize for interaction'
        'verify-install:Verify Android install' 'repair-install:Repair install'
        'focus-move:Move focus (tvOS)' 'focus-select:Select focused (tvOS)'
        'remote:Siri Remote button (tvOS)'
    )

    local -a config_commands
    config_commands=(
        'window-mode:Set window management mode'
        'show:Show all config'
        'displays:List connected displays'
        'reserve:Manage device reservations'
    )

    _arguments -C \\
        '1:command:->command' \\
        '*::arg:->args'

    case "$state" in
        command)
            _describe 'command' commands
            ;;
        args)
            case "${words[1]}" in
                claim)
                    _arguments \\
                        '1:platform:->platform' \\
                        '--version[OS version]:version' \\
                        '--form-factor[Form factor]:factor:(phone tablet watch tv vision)' \\
                        '--real[Prefer real device]' \\
                        '--device[Specific device id, current name, or alias]:device' \\
                        '--show[Keep window visible]' \\
                        '--label[Human label]:label'
                    case "$state" in
                        platform) _describe 'platform' platforms ;;
                    esac
                    ;;
                do)
                    _arguments \\
                        '1:session:->session' \\
                        '2:command:->do_cmd' \\
                        '*:args'
                    case "$state" in
                        session)
                            local -a sessions
                            sessions=($(simemu sessions --json 2>/dev/null | python3 -c "import sys,json; [print(s['session']) for s in json.load(sys.stdin)]" 2>/dev/null))
                            _describe 'session' sessions
                            ;;
                        do_cmd) _describe 'do command' do_commands ;;
                    esac
                    ;;
                config)
                    _describe 'config command' config_commands
                    ;;
                sessions)
                    _arguments '--json[Output as JSON]'
                    ;;
                status)
                    _arguments '--json[Output as JSON]'
                    ;;
            esac
            ;;
    esac
}

compdef _simemu simemu
'''


def bash_completion() -> str:
    """Generate bash completion script."""
    return '''_simemu() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    commands="claim do sessions status config serve daemon maintenance menubar create idle-shutdown rename relabel"
    platforms="ios android macos iphone ipad pixel watch tv appletv vision mac"
    do_commands="install launch terminate uninstall reset-app tap swipe long-press key input a11y-tap screenshot proof url maestro appearance rotate status-bar boot show hide done renew help build env dismiss-alert accept-alert video-start video-stop present stabilize verify-install repair-install focus-move focus-select remote"

    case "${COMP_CWORD}" in
        1)
            COMPREPLY=($(compgen -W "${commands}" -- "${cur}"))
            ;;
        2)
            case "${prev}" in
                claim) COMPREPLY=($(compgen -W "${platforms}" -- "${cur}")) ;;
                do)
                    local sessions=$(simemu sessions --json 2>/dev/null | python3 -c "import sys,json; [print(s['session']) for s in json.load(sys.stdin)]" 2>/dev/null)
                    COMPREPLY=($(compgen -W "${sessions}" -- "${cur}"))
                    ;;
                config) COMPREPLY=($(compgen -W "window-mode show displays reserve" -- "${cur}")) ;;
            esac
            ;;
        3)
            if [[ "${COMP_WORDS[1]}" == "do" ]]; then
                COMPREPLY=($(compgen -W "${do_commands}" -- "${cur}"))
            fi
            ;;
    esac
}

complete -F _simemu simemu
'''
