import test from 'node:test';
import assert from 'node:assert/strict';
import {nativePlan,placementMatrix} from '../ifcPlanRepresentations.js';
const handle=value=>({type:5,value});
test('native door arc retains actual placement and Cartesian trim endpoints',()=>{
  const records=new Map([
    [1,{Coordinates:[1000,2000,0]}],[2,{Location:handle(1)}],[3,{RelativePlacement:handle(2)}],
    [4,{Coordinates:[0,0]}],[5,{Location:handle(4)}],
    [6,{type:'IFCCIRCLE',Position:handle(5),Radius:900}],
    [7,{Coordinates:[900,0]}],[8,{Coordinates:[0,900]}],
    [9,{type:'IFCTRIMMEDCURVE',BasisCurve:handle(6),Trim1:[handle(7)],Trim2:[handle(8)],SenseAgreement:true}],
    [10,{RepresentationIdentifier:'FootPrint',Items:[handle(9)]}],
    [11,{Representations:[handle(10)]}],
  ]);
  const api={GetLine:(_,id)=>records.get(id),GetNameFromTypeCode:type=>({IFCTRIMMEDCURVE:'IfcTrimmedCurve',IFCCIRCLE:'IfcCircle'}[type]??type)};
  const segments=nativePlan(api,0,{Representation:handle(11),ObjectPlacement:handle(3)},.001,{x:0,y:0});
  assert.ok(segments.length>20);
  assert.deepEqual(segments[0][0],[1.9000000000000001,-2]);
  assert.ok(Math.abs(segments.at(-1)[1][0]-1)<1e-9);
  assert.ok(Math.abs(segments.at(-1)[1][1]+2.9)<1e-9);
  assert.equal(placementMatrix(api,0,handle(3)).elements[12],1000);
  records.get(9).Trim1=[handle('0.')];records.get(9).Trim2=[handle(String(Math.PI/2))];
  assert.deepEqual(nativePlan(api,0,{Representation:handle(11),ObjectPlacement:handle(3)},.001,{x:0,y:0}),segments);
});
