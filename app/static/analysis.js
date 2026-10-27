/* On-demand lot analysis. No credentials or provider calls in the browser. */
"use strict";
window.LotAnalysis = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const money = n => n == null ? "не рассчитано" : `${Number(n).toLocaleString("ru-RU", {maximumFractionDigits: 2})} ₽`;
  const labels = {
    transport_rub: "Доставка, ₽", inspection_rub: "Осмотр, ₽", fees_fixed_rub: "Фиксированные сборы, ₽",
    buyer_fee_pct: "Комиссия покупки, %", selling_fee_pct: "Комиссия продажи, %", storage_rub: "Хранение за весь срок, ₽",
    other_rub: "Прочие расходы, ₽", tax_reserve_rub: "Резерв на налоги, ₽", risk_reserve_rub: "Резерв рисков, ₽",
    repair_low_rub: "Ремонт: минимум, ₽", repair_base_rub: "Ремонт: базовый, ₽", repair_high_rub: "Ремонт: максимум, ₽",
    resale_discount_pct: "Скидка к цене объявления, %", capital_rate_pct_year: "Стоимость капитала, % годовых",
    holding_months: "Срок владения, месяцев", target_profit_rub: "Целевая прибыль, ₽"
  };
  const vehicles = {make:"Марка", model:"Модель", year:"Год", mileage_km:"Пробег, км", generation:"Поколение",
    engine:"Двигатель (точное обозначение)", transmission:"КПП", body_type:"Кузов", region:"Регион"};
  const decisions = {insufficient_data:"Нужны дополнительные данные", conservative_loss_or_break_even:"В осторожном сценарии прибыли нет",
    positive_under_assumptions:"Осторожный сценарий положительный при заданных допущениях",
    target_met_under_assumptions:"Целевая прибыль достигнута при заданных допущениях",
    below_target_under_assumptions:"Осторожный сценарий ниже целевой прибыли"};
  const kinds = {reported_fact:"Сообщение источника", visible_observation:"Наблюдение по фото", inference:"Предположение"};
  let dispose = () => {};
  const field = (key, label, value="", type="text") => `<label>${esc(label)}<input class="in" name="${esc(key)}" type="${type}" value="${esc(value)}" ${type === "number" ? 'min="0" step="any"' : 'maxlength="160"'}></label>`;
  const list = items => items?.length ? `<ul>${items.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const link = (url, title) => {
    try {
      const u = new URL(url);
      if (u.protocol !== "https:" || !["auto.ru","drom.ru","avito.ru"].some(d => u.hostname === d || u.hostname.endsWith("."+d))) return esc(title);
      return `<a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`;
    } catch { return esc(title); }
  };

  function reportHTML(report, stale=false) {
    const market = report.market, economics = report.economics, condition = report.condition;
    return `${stale ? '<p class="analysis-warning">Лот или выбранные фотографии изменились после анализа. Отчёт требует обновления.</p>' : ""}
      <p class="muted">${report.mode === "offline" ? "Ручной расчёт без API" : `DeepSeek · ${esc(report.model)}`} · ${esc(report.created_at)}</p>
      ${condition ? `<h4>Наблюдения о состоянии</h4><p>${esc(condition.summary)}</p>
        ${list(condition.findings.map(x=>`${kinds[x.kind] || x.kind}: ${x.text} [${x.evidence_id}]${x.quote ? " · «"+x.quote+"»" : ""}`))}
        <h4>Что неизвестно</h4>${list(condition.unknowns)}<h4>Проверить на осмотре</h4>${list(condition.inspection_checks)}` : ""}
      <h4>Цены предложений</h4><p>Подходящих аналогов: ${esc(market.sample_size)}. Средняя: <b>${money(market.mean_rub)}</b>; медиана: ${money(market.median_rub)}.</p>
      <p>Квартили Q25–Q75: ${money(market.low_rub)} — ${money(market.high_rub)}. Это цены объявлений.</p>
      ${list(market.warnings)}
      <details><summary>Аналоги и исключения</summary>
        ${market.comparables.map(c=>`<p>${link(c.url,c.title)} · ${money(c.price_rub)} · ${esc(c.observed_at)} · ${c.provenance === "user_entered" ? "введено вручную" : "цитата загруженной страницы"}</p>`).join("")}
        ${market.excluded.map(c=>`<p>${link(c.url,c.title)} · исключён: ${esc(c.reason)}</p>`).join("")}
        ${(market.sources || []).map(s=>`<p>${link(s.url,s.title || s.url)} · ${esc(s.status)}${s.reason ? ": "+esc(s.reason) : ""}${s.price_quote ? " · «"+esc(s.price_quote)+"»" : ""}</p>`).join("")}
      </details>
      <h4>Экономика участия</h4><p><b>${esc(decisions[economics.decision] || economics.decision)}</b></p>
      <p>Плановая ставка: ${money(economics.bid_price_rub)}. Вероятность победы: неизвестна — нет проверенной истории конкурирующих ставок.</p>
      <div class="analysis-scenarios">${economics.scenarios.map(s=>`<article><h4>${({conservative:"Осторожный",base:"Базовый",optimistic:"Оптимистичный"})[s.name]}</h4>
        ${s.status !== "calculated" ? `<p>Уточните: ${esc(s.missing.map(k=>labels[k] || ({market_comparables:"не менее трёх подходящих аналогов",bid_price_rub:"плановая ставка"})[k] || k).join(", "))}</p>` :
          `<p>Продажа после скидки: ${money(s.resale_price_rub)}</p><p>Все затраты: ${money(s.total_cost_rub)}</p><p><b>Прибыль: ${money(s.profit_rub)}</b></p>
           <p>ROI: ${esc(s.roi_pct)}%</p><p>Ставка без убытка: ${s.break_even_bid.feasible ? money(s.break_even_bid.bid_rub) : "не достигается"}</p>
           <p>Предел ставки для цели: ${s.max_bid_for_target ? (s.max_bid_for_target.feasible ? money(s.max_bid_for_target.bid_rub) : "цель не достигается") : "задайте целевую прибыль"}</p>`}</article>`).join("")}</div>
      ${list(economics.notes)}${list(report.limitations)}
      <details><summary>Сохранённые допущения и расход API</summary><pre>${esc(JSON.stringify({vehicle:report.vehicle,costs:economics.assumptions,usage:report.usage || {requests:0}},null,2))}</pre></details>`;
  }

  function shell(lot) {
    const photos = (lot.photos || []).filter(p=>p.local_path && p.status === "ok");
    return `<section id="lotAnalysis" class="analysis admin-only"><h3>Анализ лота с DeepSeek</h3>
      <p class="analysis-note">ИИ описывает видимые признаки и сообщения источников. Скрытые дефекты проверяются на осмотре; прибыль рассчитывается по вашим расходам.</p>
      <p data-ai-status role="status">Проверка настройки…</p><form data-analysis-form>
        <div class="analysis-fields">${Object.entries(vehicles).map(([k,v])=>field("vehicle."+k,v,lot[k] ?? lot.specs_data?.[k] ?? "",["year","mileage_km"].includes(k)?"number":"text")).join("")}
          ${field("bid_price_rub","Плановая ставка, ₽ (пусто — текущая цена)","","number")}</div>
        <details><summary>Расходы и целевая прибыль</summary><p class="muted">Пусто = неизвестно. Введите 0, если расход осознанно не учитывается. Ремонт: минимум ≤ базовый ≤ максимум.</p>
          <div class="analysis-fields">${Object.entries(labels).map(([k,v])=>field("costs."+k,v,"","number")).join("")}</div>
        </details>
        <details><summary>Аналоги вручную · расчёт работает без ключа</summary><p class="muted">Добавьте минимум три разных автомобиля. Цена — полная цена предложения. Укажите известные характеристики; при несовпадении с целевыми аналог исключается.</p>
          <div data-comparables></div><button class="btn btn-ghost" type="button" data-add-comparable>Добавить аналог</button>
        </details>
        <details><summary>Фотографии для анализа (${photos.length})</summary><p class="muted">Снимки из поиска могут относиться к другому автомобилю. Выберите только проверенные фото; метаданные файлов удаляются перед передачей. Убедитесь, что на снимках нет персональных документов.</p>
          <div class="analysis-photos">${photos.map(p=>`<label><input type="checkbox" name="photo_id" value="${p.id}"><img loading="lazy" src="/data/${esc(p.thumb_path || p.local_path)}" alt="Фото ${p.id}"><span>№ ${p.id} · ${esc(p.source_site || "источник неизвестен")}</span></label>`).join("") || "Нет доступных фотографий"}</div>
          <label class="analysis-check"><input type="checkbox" name="confirm_photo_identity">Подтверждаю, что выбранные фото относятся к этому лоту</label>
        </details>
        <label>Вопрос по лоту<textarea class="in" name="question" maxlength="1500" rows="2">Оцени видимые риски и экономику участия по доступным данным.</textarea></label>
        <label class="analysis-check"><input type="checkbox" name="search_market" checked>Искать аналоги через DeepSeek Search/Fetch</label>
        <label class="analysis-check"><input type="checkbox" name="consent_to_external">Разрешаю передать в DeepSeek очищенное описание, сведения о машине, вопрос и выбранные фото. Автоматическая очистка текста не гарантирует удаление всех персональных данных.</label>
        <div class="analysis-actions"><button class="btn btn-primary" type="submit" data-analyze disabled>Запустить анализ</button>
          <button class="btn btn-ghost" type="button" data-calculate>Рассчитать без API</button></div>
      </form><p data-run-status role="status" aria-live="polite"></p><div data-report></div>
      <details><summary>История запусков</summary><div data-history></div></details></section>`;
  }

  function mount(root, lot, api) {
    dispose();
    if (!root) return;
    let live=true, timer=null, busy=false;
    dispose=()=>{ live=false; clearTimeout(timer); };
    const alive=()=>live && root.isConnected;
    const get=s=>root.querySelector(s), form=get("form"), runStatus=get("[data-run-status]"), analyze=get("[data-analyze]");
    let configured=false;
    const showError=e=>{if(alive()) runStatus.textContent=e.message;};
    const showReport=run=>{if(alive() && run?.report) get("[data-report]").innerHTML=reportHTML(run.report,run.stale);};
    const lock=on=>{busy=on; analyze.disabled=!configured || on;};
    const post=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    function collect() {
      const value=name=>form.elements.namedItem(name)?.value?.trim() || "";
      const numeric=name=>value(name)==="" ? null : value(name); // Decimal strings preserve cents.
      const vehicle=Object.fromEntries(Object.keys(vehicles).map(k=>[k,["year","mileage_km"].includes(k)?(value("vehicle."+k)===""?null:Number(value("vehicle."+k))):value("vehicle."+k)]));
      const costs=Object.fromEntries(Object.keys(labels).map(k=>[k,numeric("costs."+k)]));
      const manual_comparables=[...get("[data-comparables]").children].map(row=>{
        const v=k=>row.querySelector(`[data-c="${k}"]`).value.trim();
        const required=["url","price_rub","make","model","year","observed_at"];
        if(required.some(k=>!v(k))) throw Error("Заполните ссылку, полную цену, марку, модель, год и дату каждого аналога");
        const c={url:v("url"),title:`${v("make")} ${v("model")} ${v("year")}`,make:v("make"),model:v("model"),year:Number(v("year")),price_rub:v("price_rub"),
          observed_at:v("observed_at")+"T00:00:00Z",mileage_km:v("mileage_km")===""?null:Number(v("mileage_km")),
          full_price:row.querySelector('[data-c="full_price"]').checked,damaged:null,listing_status:"unknown"};
        for(const k of ["generation","engine","transmission","body_type","region"]) c[k]=v(k);
        return c;
      });
      return {vehicle,costs,bid_price_rub:numeric("bid_price_rub"),manual_comparables};
    }
    get("[data-add-comparable]").onclick=()=>{
      const container=get("[data-comparables]");
      if(container.children.length>=30) {runStatus.textContent="Не более 30 ручных аналогов";return;}
      const row=document.createElement("fieldset");
      row.innerHTML='<legend>Ручной аналог</legend><div class="analysis-fields">'+Object.entries({url:"Ссылка на объявление",price_rub:"Полная цена, ₽",...vehicles,observed_at:"Дата наблюдения"}).map(([k,label])=>{
        const type=k==="observed_at"?"date":["price_rub","year","mileage_km"].includes(k)?"number":"text";
        const initial=k==="observed_at"?new Date().toISOString().slice(0,10):["make","model","year"].includes(k)?form.elements.namedItem("vehicle."+k).value:"";
        return `<label>${esc(label)}<input class="in" data-c="${k}" type="${type}" value="${esc(initial)}" maxlength="2048" ${type==="number"?'min="0" step="any"':""}></label>`;
      }).join("")+'</div><label class="analysis-check"><input type="checkbox" data-c="full_price">Проверено: полная цена автомобиля, не кредитный платёж</label><button type="button" class="btn btn-ghost" data-remove>Удалить аналог</button>';
      row.querySelector("[data-remove]").onclick=()=>row.remove(); container.append(row);
    };
    async function refreshHistory() {
      const h=await api(`/api/lots/${lot.id}/analysis`);
      if(!alive()) return;
      showReport(h.last_success);
      get("[data-history]").innerHTML=h.history.map(x=>`<p>${esc(x.created_at)} · ${esc(x.status)} ${esc(x.message || "")}</p>`).join("") || "Запусков пока нет";
      if(h.latest && ["queued","running"].includes(h.latest.status)) {lock(true); await poll(h.latest.id);}
      else if(h.latest?.status==="error") runStatus.textContent=h.latest.message;
    }
    async function poll(id) {
      if(!alive()) return;
      try {
        const run=await api(`/api/lots/${lot.id}/analysis/runs/${id}`);
        if(!alive()) return;
        const stage={queued:"В очереди",condition:"Анализ состояния",market:"Поиск и проверка аналогов",calculation:"Расчёт экономики"};
        runStatus.textContent=stage[run.stage] || run.message || (run.status==="done"?"Анализ завершён":"Анализ остановлен");
        if(["queued","running"].includes(run.status)) timer=setTimeout(()=>poll(id),2000);
        else {lock(false); showReport(run); await refreshHistory();}
      } catch(e) {lock(false);showError(e);}
    }
    get("[data-calculate]").onclick=async()=>{
      try {
        if(!form.reportValidity()) return;
        const r=await post(`/api/lots/${lot.id}/analysis/calculate`,collect());
        if(alive()) {get("[data-report]").innerHTML=reportHTML(r); runStatus.textContent="Расчёт выполнен без API; в историю платных анализов не записан";}
      } catch(e) {showError(e);}
    };
    form.onsubmit=async e=>{
      e.preventDefault(); if(busy) return;
      try {
        const payload=collect();
        payload.photo_ids=[...form.querySelectorAll('[name="photo_id"]:checked')].map(x=>Number(x.value));
        for(const k of ["confirm_photo_identity","consent_to_external","search_market"]) payload[k]=form.elements.namedItem(k).checked;
        if(!payload.consent_to_external) throw Error("Подтвердите передачу данных в DeepSeek");
        if(payload.photo_ids.length && !payload.confirm_photo_identity) throw Error("Подтвердите принадлежность фото лоту");
        payload.question=form.elements.namedItem("question").value;
        payload.request_id=crypto.randomUUID();
        lock(true); runStatus.textContent="Запуск…";
        const response=await post(`/api/lots/${lot.id}/analysis`,payload);
        if(alive()) await poll(response.run.id);
      } catch(e) {lock(false);showError(e);}
    };
    (async()=>{
      try {
        const status=await api("/api/ai/status");
        if(!alive()) return;
        configured=status.configured; lock(false);
        get("[data-ai-status]").textContent=status.message+` Модель: ${status.model}. Лимит фото: ${status.max_photos}.`;
        await refreshHistory();
      } catch(e) {showError(e);}
    })();
  }
  return {shell,mount,unmount:()=>dispose(),reportHTML};
})();
