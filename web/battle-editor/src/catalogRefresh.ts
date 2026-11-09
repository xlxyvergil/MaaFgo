/** Superseded or closed selectors must never publish an obsolete catalog. */
export class LatestCatalogRequest<T> {
 private generation=0;
 private controller:AbortController|null=null;

 invalidate():void {
  this.generation++;
  this.controller?.abort();
  this.controller=null;
 }

 async refresh(load:(signal:AbortSignal)=>Promise<T>,apply:(catalog:T)=>void):Promise<boolean> {
  this.invalidate();
  const generation=this.generation,controller=new AbortController();
  this.controller=controller;
  try{
   const catalog=await load(controller.signal);
   if(controller.signal.aborted||generation!==this.generation)return false;
   apply(catalog);
   return true;
  }catch(error){
   if(controller.signal.aborted||generation!==this.generation)return false;
   throw error;
  }finally{
   if(generation===this.generation)this.controller=null;
  }
 }
}
