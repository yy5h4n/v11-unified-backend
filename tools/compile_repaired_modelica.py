#!/usr/bin/env python3
"""Compile repaired native models in a new workspace-local build directory."""
import argparse
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from d2_modelica_buildings_aixlib_adapter import MODEL_ROOT, _omc_from_environment, _runtime_environment, _library_root


def main():
    p=argparse.ArgumentParser();p.add_argument('layer',choices=['d2','d3']);a=p.parse_args()
    package,model=('D2BuildingsAixLib','TwoRoomThermoHygrometric') if a.layer=='d2' else ('D3SharedHeat','SharedHeatPumpTwoService')
    root=ROOT/'environment_repairs_v1';build=root/'build'/a.layer;build.mkdir(parents=True,exist_ok=True)
    # CMake GLOB treats brackets in the workspace name as glob metacharacters.
    # Compile in a fresh ASCII temporary directory, then retain the FMU locally.
    work=Path(tempfile.mkdtemp(prefix='home-modelica-'+a.layer+'-'))
    libraries=[MODEL_ROOT/'Modelica/ModelicaServices/package.mo',MODEL_ROOT/'Modelica/Complex.mo',MODEL_ROOT/'Modelica/Modelica/package.mo',
               _library_root('Buildings')/'package.mo',_library_root('AixLib')/'package.mo']
    commands=['loadFile('+json.dumps(str(x.resolve()))+');' for x in libraries]
    commands += ['loadString('+json.dumps(f'within; package {package} end {package};')+');',
                 'loadFile('+json.dumps(str(root/'models'/(package+'.mo')),ensure_ascii=False)+');',
                 f'translateModelFMU({package}.{model}, version="2.0", fmuType="cs", fileNamePrefix="{package}");','getErrorString();']
    mos=work/'compile.mos';mos.write_text('\n'.join(commands)+'\n')
    omc=_omc_from_environment()
    if omc is None:raise RuntimeError('OpenModelica unavailable')
    result=subprocess.run([str(omc),str(mos)],cwd=work,env=_runtime_environment(omc),text=True,capture_output=True,timeout=600)
    (work/'compile.log').write_text(result.stdout+'\n'+result.stderr)
    fmu=work/(package+'.fmu')
    translated=work/(package+'.fmutmp')
    if not fmu.exists() and (translated/'sources/CMakeLists.txt').exists():
        # The pinned OMC contains a stale absolute CMake installation path.
        # Build its generated native C using the project-local CMake instead.
        cmake=root/'tooling/cmake/data/bin/cmake'
        for command in [[str(cmake),'-S',str(translated/'sources'),'-B',str(work/'native')],
                        [str(cmake),'--build',str(work/'native'),'--parallel','2'],
                        [str(cmake),'--install',str(work/'native')]]:
            native=subprocess.run(command,text=True,capture_output=True,timeout=300)
            with (work/'native-build.log').open('a') as log:log.write(native.stdout+'\n'+native.stderr)
            if native.returncode:raise RuntimeError('Native build failed; see '+str(work/'native-build.log'))
    elif result.returncode or not fmu.exists():raise RuntimeError('Compilation failed; see '+str(work/'compile.log'))
    target=build/'active'
    if target.exists():target.rename(build/('previous-'+str(time.time_ns())))
    if fmu.exists():
        with zipfile.ZipFile(fmu) as z:z.extractall(target)
    else:shutil.copytree(translated,target)
    print(json.dumps({'layer':a.layer,'native_fmu':str(target),'log':str(work/'compile.log')}))


if __name__=='__main__':main()
