within D3SharedHeat;

// Physical D3 plant: two thermal zones and a DHW tank share one finite heat
// source. The two commands are independent services; allocation is coupled.
model SharedHeatPumpTwoService
  import Buildings;
  import AixLib;
  input Real spaceHeatingRequest(min=0, max=1);
  input Real dhwRequest(min=0, max=1);
  parameter Real heatPumpCapacityW=1500;
  parameter Real dhwDrawW=240;
  Buildings.ThermalZones.ReducedOrder.RC.OneElement roomA(
    redeclare package Medium=Buildings.Media.Air, VAir=60, hRad=5,
    nOrientations=1, nPorts=0, AWin={0}, ATransparent={0}, hConWin=2.5,
    RWin=1, gWin=0, ratioWinConRad=0, AExt={0}, hConExt=2.5, nExt=1,
    RExt={1}, RExtRem=1, CExt={1}, use_moisture_balance=false);
  AixLib.ThermalZones.ReducedOrder.RC.OneElement roomB(
    redeclare package Medium=Buildings.Media.Air, VAir=45, hRad=5,
    nOrientations=1, nPorts=0, AWin={0}, ATransparent={0}, hConWin=2.5,
    RWin=1, gWin=0, ratioWinConRad=0, AExt={0}, hConExt=2.5, nExt=1,
    RExt={1}, RExtRem=1, CExt={1}, use_moisture_balance=false);
  Modelica.Thermal.HeatTransfer.Components.HeatCapacitor dhwTank(
    C=2.0e5, T(start=318.15, fixed=true));
  Modelica.Blocks.Sources.RealExpression spaceRequest(y=1800*spaceHeatingRequest);
  Modelica.Blocks.Sources.RealExpression dhwRequestHeat(y=1200*dhwRequest);
  Modelica.Blocks.Sources.RealExpression dhwDraw(y=-(if (time >= 900 and time < 1500) or (time >= 2100 and time < 2700) then 900 else dhwDrawW));
  Modelica.Thermal.HeatTransfer.Sources.PrescribedHeatFlow heaterA;
  Modelica.Thermal.HeatTransfer.Sources.PrescribedHeatFlow heaterB;
  Modelica.Thermal.HeatTransfer.Sources.PrescribedHeatFlow dhwHeater;
  Modelica.Thermal.HeatTransfer.Sources.PrescribedHeatFlow dhwLoad;
  Real requestedSpaceHeat, requestedDhwHeat, totalRequestedHeat;
  Real allocationFactor, allocatedSpaceHeat, allocatedDhwHeat;
  output Real roomATemperature = roomA.TAir - 273.15;
  output Real roomBTemperature = roomB.TAir - 273.15;
  output Real dhwTemperature = dhwTank.T - 273.15;
  output Real allocatedSpaceHeatW = allocatedSpaceHeat;
  output Real allocatedDhwHeatW = allocatedDhwHeat;
  output Real sharedHeatPumpCapacityUsedW = totalRequestedHeat*allocationFactor;
  output Real serviceShortfallW = totalRequestedHeat*(1-allocationFactor);
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
  requestedSpaceHeat = spaceRequest.y;
  requestedDhwHeat = dhwRequestHeat.y;
  totalRequestedHeat = requestedSpaceHeat + requestedDhwHeat;
  allocationFactor = if totalRequestedHeat > heatPumpCapacityW then
    heatPumpCapacityW/totalRequestedHeat else 1.0;
  allocatedSpaceHeat = requestedSpaceHeat*allocationFactor;
  allocatedDhwHeat = requestedDhwHeat*allocationFactor;
  heaterA.Q_flow = allocatedSpaceHeat/2;
  heaterB.Q_flow = allocatedSpaceHeat/2;
  dhwHeater.Q_flow = allocatedDhwHeat;
  dhwLoad.Q_flow = dhwDraw.y;
  connect(heaterA.port, roomA.intGainsConv);
  connect(heaterB.port, roomB.intGainsConv);
  connect(dhwHeater.port, dhwTank.port);
  connect(dhwLoad.port, dhwTank.port);
end SharedHeatPumpTwoService;
