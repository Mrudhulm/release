#!/usr/bin/env python3
import os, sys, json, subprocess
from datetime import datetime

try:
    import tomllib as _toml
    def toml_loads(s): return _toml.loads(s)
except:
    try:
        import toml
        def toml_loads(s): return toml.loads(s)
    except:
        toml_loads=None

def git_show(branch, path):
    refs=[branch] if branch else []
    if branch and branch.startswith("refs/heads/"): refs.append(branch[len("refs/heads/"):])
    elif branch and not branch.startswith("refs/"): refs.append(f"refs/heads/{branch}")
    for ref in refs:
        try: return subprocess.check_output(["git","show",f"{ref}:{path}"],stderr=subprocess.DEVNULL).decode()
        except: continue
    return None

def parse_semver(v):
    if not v: return None
    core=v.split("-",1)[0]
    parts=[int(p) if p.isdigit() else 0 for p in core.split(".")]
    while len(parts)<3: parts.append(0)
    return tuple(parts[:3])

def compare_semver(a,b):
    pa,pb=parse_semver(a),parse_semver(b)
    if pa is None or pb is None: return None
    return (pa>pb)-(pa<pb)

def read_pkg_version(content):
    if not content: return None
    try: return json.loads(content).get("version")
    except: return None

def read_toml_version(content):
    if not content or not toml_loads: return None
    try: return toml_loads(content).get("tool",{}).get("poetry",{}).get("version")
    except: return None

def extract_ticket(branch_or_env):
    if not branch_or_env: return "TICKET"
    b=branch_or_env
    if b.startswith("refs/heads/"): b=b[len("refs/heads/"):]
    part=b.split("/",1)[1] if "/" in b else b
    ticket=part.split("-",1)[0]
    return ticket or "TICKET"

def create_branch(base,ticket,version,push=False):
    date=datetime.utcnow().strftime("%Y%m%d")
    branch_name=f"release/{ticket}-{version}-{date}"
    try: subprocess.check_call(["git","checkout",base])
    except:
        if "/" in base:
            try: subprocess.check_call(["git","checkout",base.split("/")[-1]])
            except: return None
        else: return None
    try:
        subprocess.check_call(["git","checkout","-b",branch_name])
        if push: subprocess.check_call(["git","push","-u","origin",branch_name])
        return branch_name
    except: return None

def main():
    feature_branch=os.getenv("FEATURE_BRANCH") or "feature/xxx"
    main_branch=os.getenv("MAIN_BRANCH") or "main"
    project_paths=os.getenv("PROJECT_PATHS","services/backend,services/frontend").split(",")
    push=os.getenv("PUSH_RELEASE_BRANCH","false").lower() in ("1","true","yes")
    ticket_env=os.getenv("TICKET_NUMBER") or feature_branch
    project_type=os.getenv("PROJECT_TYPE") or "backend"

    created_branches=[]
    projects=[]
    for path in project_paths:
        pkg_main=read_pkg_version(git_show(main_branch,f"{path}/package.json"))
        pkg_feat=read_pkg_version(git_show(feature_branch,f"{path}/package.json"))
        toml_main=read_toml_version(git_show(main_branch,f"{path}/project.toml"))
        toml_feat=read_toml_version(git_show(feature_branch,f"{path}/project.toml"))

        cmp_pkg=compare_semver(pkg_main,pkg_feat) if (pkg_main or pkg_feat) else None
        cmp_toml=compare_semver(toml_main,toml_feat) if (toml_main or toml_feat) else None

        changed=(cmp_pkg is not None and cmp_pkg<0) or (cmp_toml is not None and cmp_toml<0)
        version=pkg_feat or toml_feat or "0.0.0"
        diff={}
        if pkg_main!=pkg_feat and (pkg_main or pkg_feat): diff["package.json"]=f"{pkg_main} → {pkg_feat}"
        if toml_main!=toml_feat and (toml_main or toml_feat): diff["project.toml"]=f"{toml_main} → {toml_feat}"

        if changed:
            ticket=extract_ticket(ticket_env)
            new_branch=create_branch(feature_branch,ticket,version,push=False)
            if new_branch: created_branches.append(new_branch)
        projects.append({"name":path,"version_diff":diff})

    out={"projects":projects,"created_branches":created_branches}
    print(json.dumps(out))
    return 0 if created_branches else 2

if __name__=="__main__":
    rc=main()
    sys.exit(rc)
