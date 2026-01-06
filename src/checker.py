def read_version_from_package_json():
    import json
    with open('package.json') as f:
        data = json.load(f)
    return data.get('version')

def read_version_from_project_toml():
    from toml import load
    data = load('project.toml')
    return data.get('tool', {}).get('poetry', {}).get('version')

def check_version_changes(main_version, feature_version):
    return main_version != feature_version

def main():
    package_json_version = read_version_from_package_json()
    project_toml_version = read_version_from_project_toml()
    
    # Assuming we have a way to get the main branch version
    main_package_json_version = "1.0.0"  # Placeholder for actual main branch version
    main_project_toml_version = "1.0.0"  # Placeholder for actual main branch version

    package_json_changed = check_version_changes(main_package_json_version, package_json_version)
    project_toml_changed = check_version_changes(main_project_toml_version, project_toml_version)

    if package_json_changed or project_toml_changed:
        print("Version changes detected.")
    else:
        print("No version changes detected.")

if __name__ == "__main__":
    main()