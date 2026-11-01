import {Info,Save} from 'lucide-react';
import {Modal} from './EditorModal';
import {sameBattleFileName} from './battleFileName';
import type {BattleSource,PreparedBattleSave} from './battleSave';

export function BattleSaveDialog({name,source,pending,directory,busy,onName,onClose,onSave,onConfirm,onRename}:{
 name:string;source:BattleSource|null;pending:PreparedBattleSave|null;directory?:string;busy:boolean;
 onName:(value:string)=>void;onClose:()=>void;onSave:()=>void;onConfirm:()=>void;onRename:()=>void;
}) {
 const replacing=!!pending?.target;
 return <Modal key={replacing?'replace':'name'} title={replacing?'是否替换原文件？':source&&sameBattleFileName(source.name,name)?'保存正式方案':'另存为正式方案'} onClose={onClose} closeDisabled={busy}>
  <div className="modal-content">
   {replacing?<>
    <p>已存在方案 <strong>{pending!.target!.name}</strong>，是否用当前编辑内容替换？</p>
    <label className="field"><span>将替换的文件</span><div className="path-display">{pending!.target!.path}</div></label>
    <div className="hint"><Info size={15}/><span>替换前会备份旧文件；如果文件已被其他窗口修改，将阻止替换并保留当前编辑内容。</span></div>
    <div className="modal-bottom"><button className="button" disabled={busy} data-autofocus onClick={onClose}>取消</button><button className="button" disabled={busy} onClick={onRename}>修改文件名</button><button className="button primary" disabled={busy} onClick={onConfirm}><Save size={17}/>{busy?'正在保存…':'替换原文件'}</button></div>
   </>:<>
    <label className="field"><span>JSON 文件名</span><input autoFocus aria-label="JSON 文件名" disabled={busy} value={name} onChange={event=>onName(event.target.value)} placeholder="local_前排1_前排2_前排3.json"/></label>
    <label className="field"><span>保存位置</span><div className="path-display">{directory}\{name}</div></label>
    <div className="hint"><Info size={15}/><span>有原文件名时会默认沿用；若当前 Battle 目录存在同名文件，替换前会再次确认。</span></div>
    <div className="modal-bottom"><button className="button" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy||!name.trim()} onClick={onSave}><Save size={17}/>{busy?'正在保存…':'确认保存'}</button></div>
   </>}
  </div>
 </Modal>;
}
