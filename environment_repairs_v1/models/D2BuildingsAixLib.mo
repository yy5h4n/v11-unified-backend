within D2BuildingsAixLib;

// This experiment is assembled from released Buildings and AixLib
// thermal-zone components; room energy states are owned by those libraries.
model TwoRoomThermoHygrometric
  import Buildings;
  import AixLib;

  input Real radiatorValve(min=0, max=1)
    "Online radiator valve command supplied through the FMI co-simulation interface";

  Buildings.ThermalZones.ReducedOrder.RC.OneElement roomA(
    redeclare package Medium=Buildings.Media.Air,
    VAir=60, hRad=5, nOrientations=1, nPorts=0,
    AWin={0}, ATransparent={0}, hConWin=2.5, RWin=1, gWin=0,
    ratioWinConRad=0, AExt={0}, hConExt=2.5, nExt=1,
    RExt={1}, RExtRem=1, CExt={1}, use_moisture_balance=false);
  AixLib.ThermalZones.ReducedOrder.RC.OneElement roomB(
    redeclare package Medium=Buildings.Media.Air,
    VAir=45, hRad=5, nOrientations=1, nPorts=0,
    AWin={0}, ATransparent={0}, hConWin=2.5, RWin=1, gWin=0,
    ratioWinConRad=0, AExt={0}, hConExt=2.5, nExt=1,
    RExt={1}, RExtRem=1, CExt={1}, use_moisture_balance=false);

  Modelica.Blocks.Sources.RealExpression heaterCommand(y=1000*radiatorValve);
  Modelica.Thermal.HeatTransfer.Sources.PrescribedHeatFlow heaterA;
  Modelica.Thermal.HeatTransfer.Sources.PrescribedHeatFlow heaterB;

  output Real roomATemperature = roomA.TAir - 273.15;
  output Real roomBTemperature = roomB.TAir - 273.15;
  // RC.OneElement does not expose moisture state. The released AixLib
  // psychrometric function derives RH from model temperature and a fixed
  // indoor vapour mass fraction; no surrogate thermal state is used.
  output Real roomARelativeHumidity =
    100*AixLib.Utilities.Psychrometrics.Functions.phi_pTX(101325, roomA.TAir, 0.010);
  output Real roomBRelativeHumidity =
    100*AixLib.Utilities.Psychrometrics.Functions.phi_pTX(101325, roomB.TAir, 0.010);
  output Real heaterHeatFlow = heaterA.Q_flow + heaterB.Q_flow;

  Modelica.Blocks.Sources.RealExpression outdoorWeather(
    y=273.15 + (if time < 1200 then 10 else if time < 2400 then 0 else 8));
  Modelica.Thermal.HeatTransfer.Sources.PrescribedTemperature outdoor;
  Modelica.Thermal.HeatTransfer.Components.ThermalConductor envelopeA(G=30);
  Modelica.Thermal.HeatTransfer.Components.ThermalConductor envelopeB(G=30);
equation
  connect(outdoorWeather.y, outdoor.T);
  connect(outdoor.port, envelopeA.port_b);
  connect(outdoor.port, envelopeB.port_b);
  connect(envelopeA.port_a, roomA.intGainsConv);
  connect(envelopeB.port_a, roomB.intGainsConv);
  connect(heaterCommand.y, heaterA.Q_flow);
  connect(heaterCommand.y, heaterB.Q_flow);
  connect(heaterA.port, roomA.intGainsConv);
  connect(heaterB.port, roomB.intGainsConv);
end TwoRoomThermoHygrometric;
